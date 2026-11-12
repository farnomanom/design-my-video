"""API keys in the macOS Keychain (service 'Design My Video', account = provider name)."""
import os
import subprocess

SERVICE = "Design My Video"
ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY"}


def get_key(provider):
    env = ENV.get(provider)
    if env and os.environ.get(env):
        return os.environ[env]
    r = subprocess.run(["security", "find-generic-password", "-s", SERVICE, "-a", provider, "-w"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    return r.stdout.strip() or None


def set_key(provider, key):
    subprocess.run(["security", "add-generic-password", "-U", "-s", SERVICE, "-a", provider, "-w", key],
                   check=True, capture_output=True)


def delete_key(provider):
    subprocess.run(["security", "delete-generic-password", "-s", SERVICE, "-a", provider], capture_output=True)
