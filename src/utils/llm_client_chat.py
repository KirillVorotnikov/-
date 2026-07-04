"""
llm_client_chat.py - резервный HTTP-клиент на основе httpx.
Используется как алиас для UnifiedLLMClient для обратной совместимости.
"""
import logging
import time
import uuid
import httpx
from src.utils.llm_providers import BaseLLMClient, ResponseUsage


logger = logging.getLogger(__name__)


class ChatCompletionsClient(BaseLLMClient):
    """
    HTTP-клиент для OpenRouter/Ollama/vLLM без зависимости от OpenAI SDK.
    Полностью повторяет функционал UnifiedLLMClient (только HTTP-режим).
    """
    
    def __init__(self, config):
        self.config = config
        self.provider = config.get("provider", "openrouter").lower()
        
        # Определяем URL и заголовки в зависимости от провайдера
        default_urls = {
            "openrouter": "https://openrouter.ai/api/v1",
            "ollama": "http://localhost:11434/v1",
            "vllm": "http://localhost:8000/v1",
            "local": "http://localhost:8080/v1",
        }
        
        base_url = self.config.get("base_url", default_urls.get(self.provider, "https://openrouter.ai/api/v1"))
        
        # OpenRouter требует специальные заголовки
        extra_headers = {}
        if self.provider == "openrouter":
            extra_headers = {
                "HTTP-Referer": "https://github.com/your-repo/itext2kg",
                "X-Title": "iText2KG Pipeline",
            }
        
        self.client = httpx.Client(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {config.get('api_key', 'EMPTY')}",
                "Content-Type": "application/json",
                **extra_headers
            },
            timeout=config.get("timeout", 300.0)
        )
        
        self.model = config["model"]
        self.max_retries = config.get("max_retries", 3)
        self.is_reasoning = config.get("is_reasoning", False)
        self.last_response_id = None
    
    def _prepare_messages(self, instructions, input_data):
        """Формирует массив messages для Chat Completions API."""
        return [
            {"role": "system", "content": instructions},
            {"role": "user", "content": input_data}
        ]
    
    def _call_api(self, messages):
        """Выполняет HTTP-запрос с retry-логикой."""
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.config.get("max_completion", 4096),
            "temperature": self.config.get("temperature", 0.6),
            "stream": False
        }
        
        # Специфичные параметры для OpenRouter
        if self.provider == "openrouter" and "provider" in self.config:
            payload["provider"] = self.config["provider"]
        
        # Встроенный retry-механизм
        for attempt in range(self.max_retries):
            try:
                response = self.client.post("/chat/completions", json=payload)
                response.raise_for_status()
                data = response.json()
                
                # Извлекаем текст и очищаем от markdown
                text = data["choices"][0]["message"]["content"]
                if not text:
                    raise ValueError("Empty response from model")
                text = self._clean_json_response(text)
                
                # Формируем статистику использования
                usage_data = data.get("usage", {})
                usage = ResponseUsage()
                usage.input_tokens = usage_data.get("prompt_tokens", 0)
                usage.output_tokens = usage_data.get("completion_tokens", 0)
                usage.total_tokens = usage_data.get("total_tokens", 0)
                usage.reasoning_tokens = usage_data.get("reasoning_tokens", 0)
                
                # Генерируем локальный ID (Chat API не имеет серверного состояния)
                local_id = f"local_{uuid.uuid4().hex[:12]}"
                self.last_response_id = local_id
                
                return text, local_id, usage
                
            except Exception as e:
                logger.warning(f"API attempt {attempt + 1} failed: {e}")
                if attempt == self.max_retries - 1:
                    raise
                time.sleep(5 * (attempt + 1))
    
    def create_response(self, instructions, input_data, previous_response_id=None):
        """Основной метод генерации (контекст уже в input_data)."""
        messages = self._prepare_messages(instructions, input_data)
        return self._call_api(messages)
    
    def repair_response(self, instructions, input_data, previous_response_id=None):
        """Повторный запрос (контекст уже в input_data)."""
        return self.create_response(instructions, input_data, previous_response_id)
    
    def confirm_response(self):
        """В Chat API подтверждение не требуется."""
        pass
    
    def _clean_json_response(self, text):
        """Удаляет markdown-обёртки из ответа."""
        text = text.strip()
        
        if text.startswith("```json") and text.endswith("```"):
            text = text[7:-3].strip()
        elif text.startswith("```") and text.endswith("```"):
            text = text[3:-3].strip()
        
        return text