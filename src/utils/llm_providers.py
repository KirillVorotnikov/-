"""
llm_providers.py - фабрика LLM-клиентов и базовые классы.
Поддерживает: openrouter, ollama, vllm, local (HTTP) и local_transformers (PyTorch).
Без зависимости от OpenAI SDK.
"""
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ResponseUsage:
    """Статистика использования токенов в запросе."""
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    reasoning_tokens = 0  # Для reasoning-моделей (o1, deepseek-r1 и т.п.)


class BaseLLMClient(ABC):
    """Единый интерфейс для всех LLM-провайдеров."""
    
    @abstractmethod
    def create_response(self, instructions, input_data, previous_response_id=None):
        """Основной метод генерации ответа."""
        pass
    
    @abstractmethod
    def repair_response(self, instructions, input_data, previous_response_id=None):
        """Повторная генерация после ошибки (с подсказкой для исправления)."""
        pass
    
    @abstractmethod
    def confirm_response(self):
        """Подтверждение успешного ответа (для серверного stateful API)."""
        pass


class LLMClientFactory:
    """Фабрика для создания нужного клиента на основе конфига."""
    
    @staticmethod
    def create_client(config):
        """
        Создаёт универсальный клиент, поддерживающий все провайдеры.
        
        Поддерживаемые провайдеры:
        - openrouter, ollama, vllm, local (через HTTP)
        - local_transformers (напрямую через PyTorch)
        """
        # Проверяем корректность provider
        provider = config.get("provider", "openrouter").lower()
        valid_providers = ["openrouter", "local_transformers", "ollama", "vllm", "local"]
        
        if provider not in valid_providers:
            raise ValueError(
                f"Unknown LLM provider: '{provider}'. "
                f"Valid options: {', '.join(valid_providers)}"
            )
        
        # Проверяем наличие необходимых параметров
        if provider == "local_transformers":
            if "local_model_path" not in config:
                raise ValueError(
                    "local_model_path is required when provider='local_transformers'"
                )
        else:
            # HTTP-провайдеры требуют model
            if "model" not in config:
                raise ValueError(f"model is required when provider='{provider}'")
        
        # Используем только UnifiedLLMClient (без зависимости от OpenAI)
        from src.utils.llm_client import UnifiedLLMClient
        return UnifiedLLMClient(config)