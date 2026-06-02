import json
import asyncio
import urllib.request
import urllib.error
from typing import Dict, Any, Optional

from core import state
from core.config import OPENROUTER_API_KEY, OLLAMA_MODEL, OLLAMA_URL

class LLMProvider:
    async def query_model(self, system_prompt: str, user_prompt: str, b64_image: str) -> Optional[str]:
        raise NotImplementedError()


class OpenRouterProvider(LLMProvider):
    def __init__(self, model_id: str = "openrouter/free", max_tokens: int = 1000, max_retries: int = 3):
        self.model_id = model_id
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        }

    async def query_model(self, system_prompt: str, user_prompt: str, b64_image: str) -> Optional[str]:
        payload = {
            "model": self.model_id,
            "max_tokens": self.max_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": user_prompt
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{b64_image}"
                            }
                        }
                    ]
                }
            ],
            "response_format": {"type": "json_object"}
        }

        loop = asyncio.get_event_loop()
        res_content = None

        for attempt in range(self.max_retries):
            try:
                def call_api():
                    req_data = json.dumps(payload).encode("utf-8")
                    req = urllib.request.Request(
                        "https://openrouter.ai/api/v1/chat/completions",
                        data=req_data,
                        headers=self.headers,
                        method="POST"
                    )
                    with urllib.request.urlopen(req, timeout=25) as response:
                        res_body = response.read().decode('utf-8')
                        res_json = json.loads(res_body)
                        return res_json["choices"][0]["message"]["content"]
                
                res_content = await loop.run_in_executor(None, call_api)
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    wait_time = 5.0 * (attempt + 1)
                    state.agent_logs.append(f"Rate limited (429). Retrying in {wait_time}s (Attempt {attempt + 1}/{self.max_retries})...")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    state.agent_logs.append(f"Error calling OpenRouter: HTTP Error {e.code}: {e.reason}")
                    break
            except Exception as e:
                state.agent_logs.append(f"Error calling OpenRouter: {e}")
                break

        if res_content is None and attempt == self.max_retries - 1:
            state.agent_logs.append("Error: Exceeded maximum retries for OpenRouter API.")

        return res_content


class OllamaProvider(LLMProvider):
    def __init__(self, model_id: str = OLLAMA_MODEL, base_url: str = OLLAMA_URL):
        self.model_id = model_id
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Content-Type": "application/json",
        }

    async def query_model(self, system_prompt: str, user_prompt: str, b64_image: str) -> Optional[str]:
        # Ollama /api/chat payload for multimodal models
        payload = {
            "model": self.model_id,
            "format": "json",
            "stream": False,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": user_prompt,
                    "images": [b64_image]
                }
            ]
        }

        loop = asyncio.get_event_loop()
        res_content = None

        try:
            def call_api():
                req_data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    f"{self.base_url}/api/chat",
                    data=req_data,
                    headers=self.headers,
                    method="POST"
                )
                # Local Ollama query might take longer depending on hardware, increasing timeout
                with urllib.request.urlopen(req, timeout=60) as response:
                    res_body = response.read().decode('utf-8')
                    res_json = json.loads(res_body)
                    return res_json["message"]["content"]
            
            res_content = await loop.run_in_executor(None, call_api)
        except urllib.error.URLError as e:
            state.agent_logs.append(f"Error connecting to Ollama: {e.reason}. Ensure Ollama is running and model '{self.model_id}' is pulled.")
        except Exception as e:
            state.agent_logs.append(f"Error calling Ollama API: {e}")

        return res_content

def get_llm_provider(provider_name: str) -> LLMProvider:
    if provider_name.lower() == "ollama":
        return OllamaProvider()
    else:
        return OpenRouterProvider()
