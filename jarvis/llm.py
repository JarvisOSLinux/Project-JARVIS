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
        self.chat_history.append({
            'role': 'user',
            'content': prompt
        })

        response = ollama.chat(
            model=self.llm_model,
            messages=self.chat_history
        )["message"]["content"]

        logger.debug(f"LLM Responded:'\n{response}\n----------")

        try:
            return json.loads(response)
        
        except json.decoder.JSONDecodeError:
            return self.ask(Config.LLM_WRONG_JSON_FORMAT_MESSAGE)
        
    def reset_history(self):
        self.chat_history = list.copy(self.default_chat)
