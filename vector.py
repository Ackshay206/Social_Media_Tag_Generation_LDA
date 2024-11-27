import pandas as pd
import numpy as np
import os
import pickle
from pathlib import Path
import re
from collections import Counter, defaultdict
from typing import List, Dict, Set, Tuple
from math import log

class Vectorizer:

    # The Vectorizer class is a custom implementation of a vectorizer that was designed with assistance from
    # generative ai tools. The idea of building a custom implementation was to get more experience with the 
    # lower level details, rather than just pulling in a library.
    # 
    # Important features of how it works:
    # 
    # => n-grams and subwords: the class generates n-grams (combinations of n words) and subwords (parts of words)
    #    to capture more context and meaning from the text.
    # 
    # => BM25 weighting: the class uses the BM25 algorithm, a popular information retrieval method, to compute the
    #    importance of words in a document relative to a collection of documents. This helps in identifying
    #    which words are more significant in the context of the entire dataset.
    # 
    # => context vectors: the class also considers the context of each word by looking at surrounding words
    #    within a specified window size. This adds more depth to the vector representation.
    # 
    # => document vectors: after processing, each document (tweet) is represented as a vector, which can be
    #    used to compute similarities with other documents, aiding in tasks like finding related hashtags.

    def __init__(
            self, 
            ngram_range: Tuple[int, int] = (1, 3),
            window_size: int = 4,
            k1: float = 1.5,
            b: float = 0.75
        ):
        self.vocabulary = {}  # word -> index mapping
        self.idf = None  # inverse document frequency
        self.doc_vectors = None  # document vectors
        self.ngram_range = ngram_range
        self.window_size = window_size
        self.k1 = k1  # BM25 parameter
        self.b = b    # BM25 parameter
        self.avg_doc_length = 0
        
    def _get_ngrams(self, tokens: List[str]) -> List[str]:
        """Generate n-grams from tokens"""
        ngrams = []
        for n in range(self.ngram_range[0], self.ngram_range[1] + 1):
            for i in range(len(tokens) - n + 1):
                ngrams.append('_'.join(tokens[i:i + n]))
        return ngrams
    
    def _get_subwords(self, word: str, min_len: int = 3) -> Set[str]:
        """Generate subwords for a given word"""
        subwords = set()
        for i in range(len(word)):
            for j in range(i + min_len, min(i + 10, len(word) + 1)):
                subwords.add(word[i:j])
        return subwords
    
    def _preprocess(self, text: str) -> List[str]:
        """Convert text to lowercase, remove special chars, split into words"""
        text = text.lower()
        text = re.sub(r'[^\w\s]', ' ', text)
        tokens = text.split()
        
        # generate n-grams and subwords
        features = []
        features.extend(self._get_ngrams(tokens))
        for token in tokens:
            features.extend(self._get_subwords(token))
        
        return features
    
    def _get_context_vectors(self, tokens: List[str]) -> Dict[str, np.ndarray]:
        """Create context vectors for each token"""
        context_vectors = defaultdict(lambda: np.zeros(len(tokens)))
        
        for i, token in enumerate(tokens):
            # consider window_size tokens before and after
            start = max(0, i - self.window_size)
            end = min(len(tokens), i + self.window_size + 1)
            
            # add positional weighting
            for j in range(start, end):
                if i != j:
                    distance = abs(i - j)
                    weight = 1.0 / distance
                    context_vectors[token][j] = weight
                    
        return context_vectors
    
    def fit(self, documents: List[str]):
        """Build vocabulary and compute IDF with BM25 weighting"""
        # build vocabulary and document frequencies
        word_doc_count = Counter()
        all_words = set()
        doc_lengths = []
        
        for doc in documents:
            tokens = self._preprocess(doc)
            doc_lengths.append(len(tokens))
            words = set(tokens)
            word_doc_count.update(words)
            all_words.update(words)
        
        self.vocabulary = {word: idx for idx, word in enumerate(sorted(all_words))}
        self.avg_doc_length = np.mean(doc_lengths)
        
        # compute BM25 IDF
        num_docs = len(documents)
        self.idf = np.zeros(len(self.vocabulary))
        for word, count in word_doc_count.items():
            if word in self.vocabulary:
                # BM25 IDF formula
                self.idf[self.vocabulary[word]] = log(
                    (num_docs - count + 0.5) / (count + 0.5) + 1.0
                )
    
    def transform(self, documents: List[str]) -> np.ndarray:
        """Convert documents to vectors using improved features"""
        if not self.vocabulary or self.idf is None:
            raise ValueError("Vectorizer must be fit before transform")
        
        vectors = np.zeros((len(documents), len(self.vocabulary)))
        
        for doc_idx, doc in enumerate(documents):
            tokens = self._preprocess(doc)
            doc_length = len(tokens)
            
            # get term frequencies
            word_counts = Counter(tokens)
            
            # get context vectors
            context_vectors = self._get_context_vectors(tokens)
            
            # compute BM25 scores with context weighting
            for word, count in word_counts.items():
                if word in self.vocabulary:
                    word_idx = self.vocabulary[word]
                    
                    # BM25 term frequency component
                    tf = (count * (self.k1 + 1)) / (count + self.k1 * (1 - self.b + self.b * doc_length / self.avg_doc_length))
                    
                    # add context influence
                    if word in context_vectors:
                        context_weight = np.mean(context_vectors[word])
                        tf *= (1 + context_weight)
                    
                    vectors[doc_idx, word_idx] = tf * self.idf[word_idx]
        
        # normalize vectors
        norms = np.linalg.norm(vectors, axis=1)
        norms[norms == 0] = 1
        vectors = vectors / norms[:, np.newaxis]
        
        return vectors

class HashtagRecommender:
    def __init__(self):
        self.vectorizer = Vectorizer()
        self.vectors = None
        self.hashtags = []
        self.vector_cache_path = "cache/vectors.pkl"
        self.hashtag_cache_path = "cache/hashtags.pkl"
        
        # create cache directory if it doesn't exist
        # this helps reduce runtimes when the program is run multiple times
        os.makedirs("cache", exist_ok=True)

    def _load_and_process_csvs(self, topic: str = None) -> None:
        """Load all CSVs from data/data directory and process them"""
        all_texts = []
        all_hashtags = []
        
        data_dir = Path("data/data")
        if topic:
            # convert topic and csv names to a common format (no spaces or underscores)
            # thats because our csv filenames have underscores in them, wheras topics
            # returned by the lda model do not.
            topic_normalized = topic.replace(' ', '').replace('_', '')
            csv_files = []
            for csv_file in data_dir.glob("*.csv"):
                csv_name = csv_file.stem.replace('_', '')
                if csv_name == topic_normalized:
                    csv_files = [csv_file]
                    break
            
            # if no match found, fall back to processing all files
            if not csv_files:
                csv_files = data_dir.glob("*.csv")
        else:
            csv_files = data_dir.glob("*.csv")
        
        for csv_file in csv_files:
            df = pd.read_csv(csv_file)
            if 'text' in df.columns and 'tags' in df.columns:
                all_texts.extend(df['text'].tolist())
                all_hashtags.extend(df['tags'].str.split().tolist())

        if not all_texts:
            raise ValueError("No valid data found in CSV files. Please check that your CSV files contain 'text' and 'tags' columns.")

        self.vectorizer.fit(all_texts)
        self.vectors = self.vectorizer.transform(all_texts)
        self.hashtags = all_hashtags

        with open(self.vector_cache_path, 'wb') as f:
            pickle.dump((self.vectorizer, self.vectors), f)
        with open(self.hashtag_cache_path, 'wb') as f:
            pickle.dump(self.hashtags, f)

    def initialize(self, topic: str = None) -> None:
        """Initialize or load from cache"""
        if (os.path.exists(self.vector_cache_path) and 
            os.path.exists(self.hashtag_cache_path) and not topic):

            with open(self.vector_cache_path, 'rb') as f:
                self.vectorizer, self.vectors = pickle.load(f)
            with open(self.hashtag_cache_path, 'rb') as f:
                self.hashtags = pickle.load(f)
        else:
            self._load_and_process_csvs(topic)

    def get_top_hashtags(self, text: str, n: int = 3) -> Dict[str, float]:
        """Get top n hashtags for input text"""
        if self.vectors is None or not self.hashtags:
            raise ValueError("Please initialize the recommender first")

        # vectorize input
        query_vector = self.vectorizer.transform([text])

        similarities = np.dot(self.vectors, query_vector.T).flatten()
        k = min(5, len(self.hashtags))
        top_indices = np.argsort(similarities)[-k:][::-1]
        
        # collect hashtags from similar documents
        hashtag_counts = {}
        for idx in top_indices:
            if idx < len(self.hashtags):
                for tag in self.hashtags[idx]:
                    tag = f"#{tag}" if not tag.startswith('#') else tag
                    hashtag_counts[tag] = hashtag_counts.get(tag, 0) + 1
        
        # sometimes no hashtags are found, so we get the closest matches anyway
        if not hashtag_counts:
            for idx in top_indices:
                if idx < len(self.hashtags):
                    for tag in self.hashtags[idx][:n]:
                        tag = f"#{tag}" if not tag.startswith('#') else tag
                        hashtag_counts[tag] = 1

        # sort and return top n hashtags
        sorted_hashtags = sorted(hashtag_counts.items(), 
                               key=lambda x: x[1], 
                               reverse=True)
        return dict(sorted_hashtags[:n])

if __name__ == "__main__":
    print("Initializing hashtag recommender...")
    topic = input("Enter a topic (or leave blank for all): ").strip().replace(" ", "")
    recommender = HashtagRecommender()
    recommender.initialize(topic)
    print("Ready to recommend hashtags!")
    print("\nEnter text to get hashtag recommendations (or 'quit' to exit)")
    
    while True:
        print("\n> ", end='')
        text = input()
        
        if not text.strip():
            continue
        
        try:
            top_hashtags = recommender.get_top_hashtags(text)
            print("\nRecommended hashtags:")
            for tag, score in top_hashtags.items():
                print(f"  {tag}")
        except Exception as e:
            print(f"Error: {str(e)}")
