"""Murmure: a self-hosted voice assistant (local speech recognition and synthesis, instant tools, web search, any OpenAI-compatible LLM)."""
import os

# Instance directory: servers.json, system_prompt.txt, pronunciation.txt, voices/, plugins/, key files... (default: current directory)
HOME = os.environ.get("MURMURE_HOME", os.getcwd())
# Defaults shipped with the package (pronunciation dictionary)
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
