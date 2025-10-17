import ollama
from .config import Config
from .core.logger import get_logger
import json

logger = get_logger(__name__)

class LLM:
    def __init__(self, system, release, version, machine, shell):
        self.llm_model = Config.LLM_MODEL
        self.default_chat = [
                    {
                        'role': 'system',
                        'content': Config.LLM_RULE.format(system=system, release=release, version=version, machine=machine, shell=shell),
                    }
                ]
        self.chat_history = list.copy(self.default_chat)

        logger.info("LLM: Initiating Preload...")
        # Start preload
        ollama.chat(
            model=Config.LLM_MODEL,
            messages=self.chat_history
        )
        logger.info("LLM: Initiation Complete!")
    
    def ask(self, prompt):
        logger.info(f"LLM: Received prompt (length: {len(prompt)} chars)")
        logger.debug(f"LLM: Prompt content:\n{prompt}\n----------")
        
        self.chat_history.append({
            'role': 'user',
            'content': prompt
        })

        logger.info(f"LLM: Calling ollama.chat with model '{self.llm_model}'...")
        response = ollama.chat(
            model=self.llm_model,
            messages=self.chat_history
        )["message"]["content"]

        logger.info(f"LLM: Received response (length: {len(response)} chars)")
        logger.debug(f"LLM: Response content:\n{response}\n----------")

        try:
            parsed = json.loads(response)
            logger.info(f"LLM: Parsed - user_request={parsed.get('user_request')}, output={parsed.get('output')[:60]}...")
            return parsed
        
        except json.decoder.JSONDecodeError as e:
            logger.warning(f"LLM: Failed to parse JSON response: {e}")
            logger.info("LLM: Re-prompting with error correction message...")
            return self.ask(Config.LLM_WRONG_JSON_FORMAT_MESSAGE)
        
    def reset_history(self):
        self.chat_history = list.copy(self.default_chat)
