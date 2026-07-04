"""
llm_client.py - универсальный LLM-клиент без зависимости от OpenAI SDK.
Поддерживает openrouter, ollama, vllm и локальные PyTorch-модели.
"""
import logging
import time
import json
import httpx
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM, GenerationConfig
from src.utils.llm_providers import BaseLLMClient, ResponseUsage


logger = logging.getLogger(__name__)


class UnifiedLLMClient(BaseLLMClient):
    """Универсальный клиент для всех LLM-провайдеров."""
    
    def __init__(self, config):
        self.config = config
        self.provider = config.get("provider", "openrouter").lower()
        
        # Задержка между успешными запросами (для обхода rate limits бесплатных моделей)
        self.request_delay = config.get("request_delay_seconds", 0)
        
        if self.provider == "local_transformers":
            self._init_local_model()
        else:
            self._init_http_client()
    
    def _init_http_client(self):
        """Инициализация HTTP-клиента."""
        default_urls = {
            "openrouter": "https://openrouter.ai/api/v1",
            "ollama": "http://localhost:11434/v1",
            "vllm": "http://localhost:8000/v1",
            "local": "http://localhost:8080/v1",
        }
        
        self.base_url = self.config.get("base_url", default_urls.get(self.provider, "https://openrouter.ai/api/v1"))
        self.api_key = self.config.get("api_key", "EMPTY")
        self.model = self.config["model"]
        self.max_tokens = self.config.get("max_completion", 4096)
        self.temperature = self.config.get("temperature", 0.6)
        self.timeout = self.config.get("timeout", 300)
        self.is_reasoning = self.config.get("is_reasoning", False)
        self.max_retries = self.config.get("max_retries", 5)  # Увеличили до 5 для 429
        
        if self.provider == "openrouter":
            if not self.api_key or self.api_key in ["EMPTY", "sk-or-...", "sk-..."]:
                raise ValueError("OpenRouter API key is not configured.")
        
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        if "openrouter" in self.base_url:
            self.headers["HTTP-Referer"] = "https://github.com/your-repo/itext2kg"
            self.headers["X-Title"] = "iText2KG Pipeline"
        
        self.client = httpx.Client(timeout=self.timeout)
        logger.info(f"HTTP Client initialized: {self.provider} ({self.base_url})")
    
    def _init_local_model(self):
        """Загрузка локальной PyTorch-модели."""
        model_path = self.config["local_model_path"]
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info(f"Loading local model from {model_path} to {self.device}...")
        
        self.tokenizer = AutoTokenizer.from_pretrained(
            model_path, trust_remote_code=True, local_files_only=True
        )
        self.model = AutoModelForCausalLM.from_pretrained(
            model_path, trust_remote_code=True, local_files_only=True
        )
        self.model.to(self.device)
        self.model.eval()
        
        self.max_tokens = self.config.get("max_completion", 4096)
        self.temperature = self.config.get("temperature", 0.6)
        logger.info("Local PyTorch model loaded successfully.")
    
    def create_response(self, instructions, input_data, previous_response_id=None):
        """Единая точка входа для генерации ответа."""
        # Задержка между запросами (для free-моделей)
        if self.request_delay > 0 and self.provider != "local_transformers":
            time.sleep(self.request_delay)
        
        if self.provider == "local_transformers":
            return self._generate_local(instructions, input_data)
        else:
            return self._generate_http(instructions, input_data)
    
    def _generate_http(self, instructions, input_data):
        """Генерация через HTTP с улучшенной обработкой 429."""
        url = f"{self.base_url}/chat/completions"
        
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": input_data}
            ],
            "max_tokens": self.max_tokens,
            "stream": False
        }
        
        if not self.is_reasoning and self.temperature is not None:
            payload["temperature"] = self.temperature

        for attempt in range(self.max_retries):
            try:
                response = self.client.post(url, json=payload, headers=self.headers)
                
                # Обработка ошибок
                if response.status_code == 401:
                    raise ValueError(f"401 Unauthorized: {response.text}")
                
                if response.status_code == 400:
                    raise ValueError(f"400 Bad Request: {response.text}")
                
                # УЛУЧШЕННАЯ ОБРАБОТКА 429
                if response.status_code == 429:
                    error_data = {}
                    try:
                        error_data = response.json()
                    except:
                        pass
                    
                    metadata = error_data.get("error", {}).get("metadata", {})
                    
                    # Извлекаем реальное время ожидания из Retry-After
                    retry_after = metadata.get("retry_after_seconds")
                    
                    # Если Retry-After не указан, используем прогрессивную задержку
                    if retry_after is None:
                        retry_after = 15 * (attempt + 1)
                    else:
                        # Добавляем буфер 3 секунды для надёжности
                        retry_after = int(retry_after) + 3
                    
                    wait_time = min(retry_after, 120)  # Максимум 2 минуты
                    
                    if attempt == self.max_retries - 1:
                        raise ValueError(
                            f"429 Too Many Requests after {self.max_retries} attempts. "
                            f"Last retry_after: {retry_after}s. Details: {response.text}"
                        )
                    
                    logger.warning(
                        f"Rate limit exceeded (attempt {attempt + 1}/{self.max_retries}). "
                        f"Waiting {wait_time}s before retry..."
                    )
                    print(f"⏳ Rate limit hit. Waiting {wait_time} seconds before retry {attempt + 1}/{self.max_retries}...")
                    
                    time.sleep(wait_time)
                    continue  # Переходим к следующей попытке
                
                response.raise_for_status()
                data = response.json()
                
                text = data["choices"][0]["message"]["content"].strip()
                text = self._clean_json_response(text)
                
                usage_data = data.get("usage", {})
                usage = ResponseUsage()
                usage.input_tokens = usage_data.get("prompt_tokens", 0)
                usage.output_tokens = usage_data.get("completion_tokens", 0)
                usage.total_tokens = usage_data.get("total_tokens", 0)
                usage.reasoning_tokens = usage_data.get("reasoning_tokens", 0)
                
                return text, f"http_{attempt}", usage
                
            except httpx.HTTPStatusError as e:
                error_details = e.response.text
                logger.error(f"HTTP attempt {attempt + 1} failed: {error_details}")
                if attempt == self.max_retries - 1:
                    raise ValueError(f"API request failed. Details: {error_details}")
                time.sleep(5 * (attempt + 1))
                
            except Exception as e:
                logger.warning(f"HTTP attempt {attempt + 1} failed: {e}")
                if attempt == self.max_retries - 1:
                    raise
                time.sleep(5 * (attempt + 1))
    
    def _generate_local(self, instructions, input_data):
        """Генерация через локальный PyTorch."""
        messages = [
            {"role": "system", "content": instructions},
            {"role": "user", "content": input_data}
        ]
        prompt = self.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = self.tokenizer(prompt, return_tensors="pt").to(self.device)
        input_tokens = inputs.input_ids.shape[1]
        
        gen_config = GenerationConfig(
            max_new_tokens=self.max_tokens,
            temperature=self.temperature,
            top_p=0.9,
            do_sample=self.temperature > 0,
            eos_token_id=self.tokenizer.eos_token_id,
            repetition_penalty=1.15
        )
        
        with torch.no_grad():
            output = self.model.generate(**inputs, generation_config=gen_config)
        
        new_tokens = output[0][input_tokens:]
        answer = self.tokenizer.decode(new_tokens, skip_special_tokens=True)
        output_tokens = len(new_tokens)
        
        if "</think>" in answer:
            answer = answer.split("</think>", 1)[-1].strip()
        
        answer = self._clean_json_response(answer)
        
        usage = ResponseUsage()
        usage.input_tokens = input_tokens
        usage.output_tokens = output_tokens
        usage.total_tokens = input_tokens + output_tokens
        usage.reasoning_tokens = 0
        
        return answer, "local_response", usage
    
    def repair_response(self, instructions, input_data, previous_response_id=None):
        """Повторная генерация после ошибки."""
        return self.create_response(instructions, input_data)
    
    def confirm_response(self):
        """Подтверждение ответа (пустышка для HTTP)."""
        pass
    
    def _clean_json_response(self, text):
        """Очищает ответ от markdown-обёрток."""
        text = text.strip()
        if text.startswith("```json") and text.endswith("```"):
            text = text[7:-3].strip()
        elif text.startswith("```") and text.endswith("```"):
            text = text[3:-3].strip()
        return text