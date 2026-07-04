"""
llm_embeddings.py - получение векторных эмбеддингов.
Поддерживает только локальные модели (sentence-transformers).
Для онлайн-моделей (OpenAI, OpenRouter) используйте отдельные клиенты.
"""
import logging
import numpy as np
from sentence_transformers import SentenceTransformer


logger = logging.getLogger(__name__)


class LocalEmbeddingsClient:
    """Локальный клиент эмбеддингов (без API, работает оффлайн)."""
    
    def __init__(self, config):
        """Загружает модель sentence-transformers из локального кэша."""
        model_name = config.get("embedding_model", "sentence-transformers/all-MiniLM-L6-v2")
        logger.info(f"Loading local embedding model: {model_name}")
        
        self.model = SentenceTransformer(model_name)
        self.dimension = self.model.get_sentence_embedding_dimension()
        logger.info(f"Embedding model loaded. Dimension: {self.dimension}")
    
    def get_embeddings(self, texts):
        """
        Получает эмбеддинги для списка текстов.
        Возвращает нормализованные векторы (косинусное сходство = скалярное произведение).
        """
        if not texts:
            return np.array([])
        
        # Заменяем пустые строки пробелами (SentenceTransformer не любит пустые)
        valid_texts = [t if t.strip() else " " for t in texts]
        
        logger.info(f"Generating embeddings for {len(valid_texts)} texts...")
        
        embeddings = self.model.encode(
            valid_texts,
            convert_to_numpy=True,
            normalize_embeddings=True,  # Векторы единичной длины
            batch_size=256,
            show_progress_bar=True
        )
        
        return embeddings


def get_embeddings_client(config):
    """
    Фабрика клиентов эмбеддингов.
    В текущей версии возвращает только локальный клиент.
    """
    return LocalEmbeddingsClient(config)


def get_embeddings(texts, config):
    """
    Обёртка для обратной совместимости с dedup.py и refiner.py.
    Создаёт клиент и сразу получает эмбеддинги.
    """
    client = LocalEmbeddingsClient(config)
    return client.get_embeddings(texts)


def cosine_similarity_batch(embeddings1, embeddings2):
    """
    Вычисляет косинусное сходство между двумя наборами эмбеддингов.
    Для нормализованных векторов это просто скалярное произведение.
    """
    return np.dot(embeddings1, embeddings2.T)