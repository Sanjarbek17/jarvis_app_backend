import os
import sys
import base64
import json
import time
import requests

# Configuration
BACKEND_URL = "http://localhost:10555"
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

if not OPENROUTER_API_KEY:
    print("Error: Please set the OPENROUTER_API_KEY environment variable.", file=sys.stderr)
    sys.exit(1)

def get_screenshot_base64() -> str:
    """Trigger a screenshot on the backend and return the base64-encoded image."""
    try:
        # Request new screenshot
        res = requests.post(f"{BACKEND_URL}/screenshot", timeout=15)
        if res.status_code != 200:
            print(f"Failed to capture screenshot. Backend returned status code {res.status_code}")
            return ""
        
        # Download the PNG bytes
        img_bytes = res.content
        return base64.b64encode(img_bytes).decode("utf-8")
    except Exception as e:
        print(f"Error fetching screenshot: {e}")
        return ""

def execute_action(action: str, **kwargs) -> bool:
    """Send execution command to the backend."""
    payload = {"action": action, **kwargs}
    try:
        res = requests.post(f"{BACKEND_URL}/execute", json=payload, timeout=10)
        if res.status_code == 200:
            print(f"Executed: {action} with args {kwargs}")
            return True
        else:
            print(f"Failed to execute command. Status: {res.status_code}, Response: {res.text}")
            return False
    except Exception as e:
        print(f"Error executing command: {e}")
        return False

SYSTEM_PROMPT = """You are a phone assistant controller. You help users control their phone since their touchscreen is broken.
You will be given the user's overall goal, the history of steps, and a screenshot of the current phone screen.
Your job is to look at the screen and decide the next single step.

Available actions:
- {"action": "open", "text": "string"} -> Opens an application by name (e.g. "Telegram", "Maps", "Chrome"). Prefer this over coordinates for opening apps.
- {"action": "click", "label": "string"} -> Finds and clicks a button or text label matching this string on the screen (e.g. "Chats", "Search", "Next"). Prefer this over coordinates for clicking text buttons.
- {"action": "tap", "x": float, "y": float} -> Taps a location on the screen. Coordinates must be normalized (0.0 to 1.0). Use this if no clear text/label is available to click.
- {"action": "swipe", "x": float, "y": float, "x2": float, "y2": float, "duration": int} -> Swipes from (x, y) to (x2, y2).
- {"action": "write", "text": "string"} -> Types text into the focused input field.
- {"action": "back"} -> Presses the back button.
- {"action": "home"} -> Presses the home button.
- {"action": "sleep", "duration": int} -> Wait/sleep for a duration in milliseconds.
- {"action": "finish", "message": "string"} -> Stop when the goal is achieved or cannot proceed.

Prefer using semantic actions like "open" or "click" (by text label) when applicable as they are much more robust than tapping normalized coordinates.

Respond ONLY with a valid JSON object of the action, containing "thought" and "action" fields.
Example response:
{
  "thought": "I want to open Telegram, so I'll use the open app action directly.",
  "action": "open",
  "text": "Telegram"
}
"""

def query_agent(goal: str, history: list, base64_image: str) -> dict:
    """Send screenshot and history to OpenRouter VLM to get the next action."""
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    
    prompt = f"Goal: {goal}\nSteps taken so far:\n"
    for i, step in enumerate(history):
        prompt += f"{i+1}. {step}\n"
    prompt += "\nDecide the next action based on the screenshot."

    payload = {
        "model": "google/gemini-2.5-flash",
        "max_tokens": 1000,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompt
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{base64_image}"
                        }
                    }
                ]
            }
        ],
        "response_format": {"type": "json_object"}
    }
    
    try:
        res = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=25
        )
        if res.status_code == 200:
            res_json = res.json()
            content = res_json["choices"][0]["message"]["content"]
            content_str = content.strip()
            if content_str.startswith("```"):
                lines = content_str.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                content_str = "\n".join(lines).strip()
            return json.loads(content_str)
        else:
            print(f"OpenRouter API error: {res.status_code} - {res.text}")
            return {}
    except Exception as e:
        print(f"Error querying OpenRouter: {e}")
        return {}

def run_loop(goal: str):
    history = []
    max_steps = 15
    
    print(f"\n[Agent] Starting run for goal: '{goal}'")
    for step_num in range(1, max_steps + 1):
        print(f"\n--- Step {step_num} ---")
        print("Capturing phone screen...")
        b64_img = get_screenshot_base64()
        if not b64_img:
            print("Could not get screen screenshot. Aborting.")
            break
            
        print("Analyzing screen with AI...")
        decision = query_agent(goal, history, b64_img)
        if not decision or "action" not in decision:
            print("Failed to get clear decision from agent. Aborting.")
            break
            
        thought = decision.get("thought", "")
        action = decision.get("action")
        
        print(f"Thought: {thought}")
        print(f"Decision: {json.dumps(decision)}")
        
        if action == "finish":
            print(f"\n[Agent] Goal completed! Message: {decision.get('message', '')}")
            break
            
        # Execute action
        args = {k: v for k, v in decision.items() if k not in ["thought", "action"]}
        success = execute_action(action, **args)
        if not success:
            print("Action execution failed. Aborting.")
            break
            
        # Log step
        history.append(f"{action} ({args}) - {thought}")
        
        # Wait for transition
        time.sleep(2.0)
    else:
        print("\n[Agent] Reached maximum step limit.")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        goal = " ".join(sys.argv[1:])
        run_loop(goal)
    else:
        while True:
            try:
                goal = input("\nEnter your phone command (or press Ctrl+C to exit): ").strip()
                if goal:
                    run_loop(goal)
            except KeyboardInterrupt:
                print("\nGoodbye!")
                break
