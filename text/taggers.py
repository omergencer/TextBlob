# -*- coding: utf-8 -*-
'''Parts-of-speech tagger implementations.'''
import random
import os.path
from collections import defaultdict
import cPickle

from .packages import nltk
from .en import tag as pattern_tag
from .exceptions import MissingCorpusException
from _perceptron import Perceptron


class BaseTagger(object):

    '''Abstract tagger class from which all taggers
    inherit from. All descendants must implement a
    `tag()` method.
    '''

    def tag(self, sentence):
        '''Return a list of tuples of the form (word, tag)
        for a given set of text.
        '''
        raise NotImplementedError('Must implement a tag() method')


class PatternTagger(BaseTagger):

    '''Tagger that uses the implementation in
    Tom de Smedt's pattern library
    (http://www.clips.ua.ac.be/pattern).
    '''

    def tag(self, sentence, tokenize=True):
        return pattern_tag(sentence, tokenize)

class NLTKTagger(BaseTagger):

    '''Tagger that uses NLTK's standard TreeBank tagger.
    NOTE: Currently supported on Python 2 only, and requires numpy.
    '''

    def tag(self, sentence, tokenize=True):
        if tokenize:
            sentence = nltk.tokenize.word_tokenize(sentence)
        try:
            tagged = nltk.tag.pos_tag(sentence)
        except LookupError as e:
            print(e)
            raise MissingCorpusException()
        return tagged


START = ['-START-', '-START2-']
END = ['-END-', '-END2-']


class PerceptronTagger(BaseTagger):
    '''Greedy Averaged Perceptron tagger'''
    def __init__(self):
        self.model = Perceptron()
        self.tagdict = {}
        self.case_sensitive = False
        self.uppers = set()
        self.titles = set()
        self.classes = set()
        upper_thresh = 0.05
        title_thresh = 0.3
        for line in open(os.path.join(os.path.dirname(__file__), 'en-case.txt')):
            word, upper, title, lower = line.split()
            upper = int(upper); title = int(title); lower = int(lower)
            total = float(upper + title + lower)
            if (upper / total) >= upper_thresh:
                self.uppers.add(word)
            if (title / total) >= title_thresh:
                self.titles.add(word)

    def tag(self, sentence, tokenize=True):
        words = nltk.word_tokenize(sentence) if tokenize else sentence.split()
        prev, prev2 = START
        context = START + [self._normalize(w) for w in words] + END
        tags = []
        for i, word in enumerate(words):
            tag = self.tagdict.get(word)
            if not tag:
                features = self._get_features(i+2, word, context, prev, prev2)
                tag = self.model.predict(features)
            tags.append(tag)
            prev2 = prev; prev = tag
        return zip(words, tags)

    def train(self, sentences, save_loc, nr_iter=5, quiet=False):
        '''Train a model from sentences, and save it at save_loc. nr_iter
        controls the number of Perceptron training iterations.'''
        self._make_tagdict(sentences, quiet=quiet)
        self.model.classes = self.classes
        prev, prev2 = START
        for iter_ in range(nr_iter):
            c = 0; n = 0
            for words, tags in sentences:
                context = START + [self._normalize(w) for w in words] + END
                for i, word in enumerate(words):
                    guess = self.tagdict.get(word)
                    if not guess:
                        feats = self._get_features(i+2, word, context, prev, prev2)
                        guess = self.model.predict(feats)
                        self.model.update(tags[i], guess, feats)
                    prev2 = prev; prev = guess
                    c += guess == tags[i]; n += 1
            random.shuffle(sentences)
            if not quiet:
                print("Iter %d: %d/%d=%.3f" % (iter_, c, n, _pc(c, n)))
        self.model.average_weights()
        # Pickle as a binary file
        cPickle.dump((self.model.weights, self.tagdict, self.classes),
                     open(save_loc, 'wb'), -1)

    def load(self, loc):
        w_td_c = cPickle.load(open(loc, 'rb'))
        self.model.weights, self.tagdict, self.classes = w_td_c
        self.model.classes = self.classes

    def _normalize(self, word):
        if '-' in word and word[0] != '-':
            return '!HYPHEN'
        elif word.isdigit() and len(word) == 4:
            return '!YEAR'
        elif word[0].isdigit():
            return '!DIGITS'
        elif not self.case_sensitive:
            return word.lower()
        else:
            return word

    def _get_features(self, i, word, context, prev, prev2):
        '''Map tokens into a feature representation, implemented as a
        {hashable: float} dict. If the features change, a new model must be
        trained.'''
        def add(name, *args):
            features[(name,) + tuple(args)] += 1
        features = defaultdict(int)
        # It's useful to have a constant feature, which acts sort of like a prior
        add('prior')
        add('curr suffix', word[-3:])
        add('curr pref1', word[0])
        add('prev tag', prev)
        add('2prev tag', prev2)
        add('prev tag+2prev tag', prev, prev2)
        add('curr w', context[i])
        add('prev tag+curr word', prev, context[i])
        add('prev w', context[i-1])
        add('prev suff', context[i-1][-3:])
        add('2prev w', context[i-2])
        add('next w', context[i+1])
        add('next suff', context[i+1][-3:])
        add('2next w', context[i+2])
        if context[i] in self.uppers:
            add('upper')
            add('upper+prev', prev)
        if context[i] in self.titles:
            add('title')
            add('title+suffix', word[-3:])
            add('title+prev', prev)
        return features

    def _make_tagdict(self, sentences, quiet=False):
        '''Make a tag dictionary for single-tag words.'''
        counts = defaultdict(lambda: defaultdict(int))
        for words, tags in sentences:
            for word, tag in zip(words, tags):
                counts[word][tag] += 1
                self.model.weights[tag] = {}
                self.classes.add(tag)
        freq_thresh = 100
        ambiguity_thresh = 0.99
        total = 0; covered = 0
        for word, tag_freqs in counts.items():
            n = 0.0; mode = 0.0
            for tag, freq in tag_freqs.items():
                if freq >= mode:
                    incumbent = tag
                    mode = freq
                n += freq; total += freq
            # Don't add rare words to the tag dictionary
            # Only add quite unambiguous words
            if n >= freq_thresh and (float(mode) / n) >= ambiguity_thresh:
                self.tagdict[word] = incumbent
                covered += n
        if not quiet:
            msg = "Cached tags for %d types (%.2fpc of tokens)"
            print(msg % (len(self.tagdict.keys()), _pc(covered, total)))

def _pc(n, d):
    return (float(n) / d) * 100
