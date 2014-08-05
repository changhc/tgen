#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Sentence planning: Generating T-trees from dialogue acts.
"""

from __future__ import unicode_literals
import heapq
from collections import deque
from UserDict import DictMixin

from alex.components.nlg.tectotpl.core.document import Document
from alex.components.nlg.tectotpl.core.node import T
from flect.logf import log_debug


class CandidateList(DictMixin):
    """List of candidate trees that can be quickly checked for membership and
    is sorted according to scores."""

    def __init__(self, members=None):
        self.queue = []
        self.members = {}
        if members:
            self.pushall(members)
        pass

    def __nonzero__(self):
        return len(self.members) > 0

    def __contains__(self, key):
        return key in self.members

    def __getitem__(self, key):
        return self.members[key]

    def __setitem__(self, key, value):
        # slow if key is in list
        if key in self:
            if value == self[key]:
                return
            queue_index = (i for i, v in enumerate(self.queue) if i[1] == key).next()
            self.queue[queue_index] = (value, key)
            self.__fix_queue(queue_index)
        else:
            heapq.heappush(self.queue, (value, key))
        self.members[key] = value

    def __delitem__(self, key):
        del self.members[key]  # this will raise an exception if the key is not there
        queue_index = (i for i, v in enumerate(self.queue) if i[1] == key).next()
        self.queue[queue_index] = self.queue[-1]
        del self.queue[-1]
        self.__fix_queue(queue_index)

    def keys(self):
        return self.members.keys()

    def pop(self):
        """Return the first item on the heap and remove it."""
        value, key = heapq.heappop(self.queue)
        del self.members[key]
        return key, value

    def peek(self):
        """Return the first item on the heap, but do not remove it."""
        value, key = self.queue[0]
        return key, value

    def push(self, key, value):
        """Push one key-value pair to the heap."""
        self[key] = value  # calling __setitem__; it will test for membership

    def pushall(self, members):
        """Push all members of the given structure to the heap
        (a list of pairs key-value or a dictionary are accepted)."""
        if isinstance(members, dict):
            members = members.iteritems()
        for key, value in members:
            self[key] = value

    def prune(self, size):
        """Trim the list to the given size, return the rest."""
        if len(self.queue) <= size:  # don't do anything if we're small enough already
            return {}
        pruned_queue = []
        pruned_members = {}
        for _ in xrange(size):
            key, val = self.pop()
            pruned_queue.append((val, key))
            pruned_members[key] = val
        remain_members = self.members
        self.members = pruned_members
        self.queue = pruned_queue
        return remain_members

    def __fix_queue(self, index):
        """Fixing a heap after change in one element (swapping elements until heap
        condition is satisfied.)"""
        down = index / 2 - 1
        up = index * 2 + 1
        if index > 0 and self.queue[index][0] < self.queue[down][0]:
            self.queue[index], self.queue[down] = self.queue[down], self.queue[index]
            self.__fix_queue(down)
        elif up < len(self.queue):
            if self.queue[index][0] > self.queue[up][0]:
                self.queue[index], self.queue[up] = self.queue[up], self.queue[index]
                self.__fix_queue(up)
            elif self.queue[index][0] > self.queue[up + 1][0]:
                self.queue[index], self.queue[up + 1] = self.queue[up + 1], self.queue[index]
                self.__fix_queue(up + 1)


class SentencePlanner(object):
    """Common ancestor of sentence planners."""

    def __init__(self, cfg):
        """Initialize, setting language, selector, and successor generator"""
        self.language = cfg.get('language', 'en')
        self.selector = cfg.get('selector', '')
        # candidate generator
        self.candgen = cfg['candgen']

    def generate_tree(self, da, gen_doc=None):
        """Generate a tree given input DA.
        @param gen_doc: if this is None, return the tree in a new Document object, otherwise append
        """
        raise NotImplementedError

    def get_target_zone(self, gen_doc):
        """Create a document if necessary, add a new bundle and a new zone into it,
        and return the newly created zone and the document (original or new)."""
        if gen_doc is None:
            gen_doc = Document()
        bundle = gen_doc.create_bundle()
        zone = bundle.create_zone(self.language, self.selector)
        return zone, gen_doc


class SamplingPlanner(SentencePlanner):
    """Random t-tree generator given DAs.

    Trainable from DA distributions
    """

    MAX_TREE_SIZE = 50

    def __init__(self, cfg):
        super(SamplingPlanner, self).__init__(cfg)
        # ranker (selecting the best candidate)
        self.ranker = None
        if 'ranker' in cfg:
            self.ranker = cfg['ranker']

    def generate_tree(self, da, gen_doc=None):
        zone, gen_doc = self.get_target_zone(gen_doc)
        root = zone.create_ttree()
        cdfs = self.candgen.get_merged_cdfs(da)
        nodes = deque([self.generate_child(root, da, cdfs[root.formeme])])
        treesize = 1
        while nodes and treesize < self.MAX_TREE_SIZE:
            node = nodes.popleft()
            if node.formeme not in cdfs:  # skip weirdness
                continue
            for _ in xrange(self.candgen.get_number_of_children(node.formeme)):
                child = self.generate_child(node, da, cdfs[node.formeme])
                nodes.append(child)
                treesize += 1
        return gen_doc

    def generate_child(self, parent, da, cdf):
        """Generate one t-node, given its parent and the CDF for the possible children."""
        if self.ranker:
            formeme, t_lemma, right = self.ranker.get_best_child(parent, da, cdf)
        else:
            formeme, t_lemma, right = self.candgen.sample(cdf)
        child = parent.create_child()
        child.t_lemma = t_lemma
        child.formeme = formeme
        if right:
            child.shift_after_subtree(parent)
        else:
            child.shift_before_subtree(parent)
        return child


class ASearchPlanner(SentencePlanner):
    """Sentence planner using A*-search."""

    MAX_ITER = 10000

    def __init__(self, cfg):
        super(ASearchPlanner, self).__init__(cfg)
        self.debug_out = None
        if 'debug_out' in cfg:
            self.debug_out = cfg['debug_out']
        self.ranker = cfg['ranker']
        self.max_iter = cfg.get('max_iter', self.MAX_ITER)
        self.max_defic_iter = cfg.get('max_defic_iter')

    def generate_tree(self, da, gen_doc=None, gold_ttree=None):
        # generate and use only 1-best
        best_ttree = self.get_best_candidates(da, 1,
                                              self.max_iter, self.max_defic_iter,
                                              gold_ttree)[0]
        # return the result
        zone, gen_doc = self.get_target_zone(gen_doc)
        zone.ttree = best_ttree
        return gen_doc

    def get_best_candidates(self, da, ncands,
                            max_iter=None, max_defic_iter=None, beam_size=None,
                            gold_ttree=None):
        """Run the A*-search generation and after it finishes, return N best candidates
        on the close list.

        @param da: the input dialogue act
        @param ncands: (maximum) number of candidates to return
        @param max_iter: maximum number of iterations for generation
        @param gold_ttree: a gold t-tree to check if it matches the current candidate
        @rtype: list
        @return: a list of the best candidates in the close list, sorted by score
        """
        # TODO add future cost ?

        # initialization
        open_list, close_list = CandidateList({T(data={'ord': 0}): 0.0}), CandidateList()
        num_iter = 0
        defic_iter = 0
        cdfs = self.candgen.get_merged_cdfs(da)
        if not max_iter:
            max_iter = self.max_iter

        # main search loop
        while open_list and num_iter < max_iter and (max_defic_iter is None
                                                     or defic_iter <= max_defic_iter):
            cand, score = open_list.pop()
            if gold_ttree and cand == gold_ttree:
                log_debug("IT %05d: CANDIDATE MATCHES GOLD" % num_iter)
            close_list.push(cand, score)
            log_debug("\n***\nIT %05d:%s\n[%6.4f]\nO: %d C: %d\n***" %
                      (num_iter, unicode(cand), score, len(open_list), len(close_list)))
            successors = self.candgen.get_all_successors(cand, cdfs)
            # add candidates with score
            open_list.pushall({s: self.ranker.score(s, da) * -1
                               for s in successors if not s in close_list})
            # pruning (if supposed to do it)
            if beam_size is not None:
                pruned = open_list.prune(beam_size)
                close_list.pushall(pruned)
            num_iter += 1
            # check where the score is higher -- on the open or on the close list
            # keep track of 'deficit' iterations (and do not allow more than the threshold)
            if open_list and close_list:
                open_best_score, close_best_score = open_list.peek()[1], close_list.peek()[1]
                if open_best_score <= close_best_score:  # scores are negative, less is better
                    defic_iter = 0
                else:
                    defic_iter += 1

            if num_iter == max_iter:
                log_debug('ITERATION LIMIT REACHED')
            elif defic_iter == max_defic_iter:
                log_debug('DEFICIT ITERATION LIMIT REACHED')

        # return the N best candidates on the close list
        result = []
        while close_list and len(result) < ncands:
            result.append(close_list.pop()[0])
        return result
