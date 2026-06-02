import json
import base64
import asyncio
import urllib.request
import urllib.error
from typing import Callable, Awaitable
from core import state
from core.config import OPENROUTER_API_KEY

SYSTEM_PROMPT = """You are a phone assistant controller. You help users control their phone since their touchscreen is broken.
You will be given the user's overall goal, the history of steps, and a screenshot of the current phone screen.
Your job is to look at the screen and decide the next single step.

Available actions:
- {"action": "open", "text": "string"} -> Opens an application by name (e.g. "Telegram", "Maps", "Chrome"). Prefer this over coordinates for opening apps.
- {"action": "click", "label": "string"} -> Finds and clicks a button or text label matching this string on the screen (e.g. "Chats", "Search", "Next").
- {"action": "tap", "x": float, "y": float} -> Taps a location on the screen. Coordinates must be normalized (0.0 to 1.0). Use this if no clear text/label is available to click, or if playing a game.
- {"action": "swipe", "x": float, "y": float, "x2": float, "y2": float, "duration": int} -> Swipes from (x, y) to (x2, y2).
- {"action": "write", "text": "string"} -> Types text into the focused input field.
- {"action": "back"} -> Presses the back button.
- {"action": "home"} -> Presses the home button.
- {"action": "sleep", "duration": int} -> Wait/sleep for a duration in milliseconds.
- {"action": "finish", "message": "string"} -> Stop when the goal is achieved or cannot proceed.

Prefer using semantic actions like "open" or "click" for standard apps.
CRITICAL: For games or custom UIs where accessibility labels are missing and semantic clicks fail, you MUST use the "tap" action with normalized x, y coordinates instead.

Respond ONLY with a valid JSON object of the action, containing "thought" and "action" fields.
Example response:
{
  "thought": "I want to open Telegram, so I'll use the open app action directly.",
  "action": "open",
  "text": "Telegram"
}
"""

async def run_agent_loop(goal: str, execute_action_cb: Callable[[dict, bool], Awaitable[None]], max_steps: int = 15):
    """
    Background loop that drives the AI assistant.
    :param goal: The user's goal string.
    :param execute_action_cb: A callback function to execute a given action payload (e.g. relay_step).
    :param max_steps: Maximum iterations.
    """
    state.agent_logs = [f"Starting agent for goal: '{goal}' (limit: {max_steps} steps)"]
    history = []
    
    try:
        for step_num in range(1, max_steps + 1):
            if not state.agent_running:
                state.agent_logs.append("Agent stopped by user.")
                break
                
            state.agent_logs.append(f"Step {step_num}: Capturing screenshot...")
            
            conn = state.active_connection
            if conn is None:
                state.agent_logs.append("Error: Main phone is not connected.")
                break
                
            state.latest_screenshot = None
            state.screenshot_event.clear()
            await conn.send_text(json.dumps({"action": "screenshot"}))
            
            try:
                await asyncio.wait_for(state.screenshot_event.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                state.agent_logs.append("Error: Screenshot timed out.")
                break
                
            if state.latest_screenshot is None:
                state.agent_logs.append("Error: Screenshot data was empty.")
                break
                
            b64_image = base64.b64encode(state.latest_screenshot).decode("utf-8")
            
            state.agent_logs.append(f"Step {step_num}: Querying OpenRouter...")
            
            headers = {
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            }
            
            prompt = f"Goal: {goal}\nSteps taken so far:\n"
            for idx, step in enumerate(history):
                prompt += f"{idx+1}. {step}\n"
            prompt += "\nDecide the next action based on the screenshot."

            payload = {
                "model": "google/gemma-4-31b-it:free",
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
                                    "url": f"data:image/png;base64,{b64_image}"
                                }
                            }
                        ]
                    }
                ],
                "response_format": {"type": "json_object"}
            }
            
            # Run the requests block in executor so it is non-blocking
            loop = asyncio.get_event_loop()
            res_content = None
            max_retries = 3
            
            for attempt in range(max_retries):
                try:
                    def call_api():
                        req_data = json.dumps(payload).encode("utf-8")
                        req = urllib.request.Request(
                            "https://openrouter.ai/api/v1/chat/completions",
                            data=req_data,
                            headers=headers,
                            method="POST"
                        )
                        with urllib.request.urlopen(req, timeout=25) as response:
                            res_body = response.read().decode('utf-8')
                            res_json = json.loads(res_body)
                            return res_json["choices"][0]["message"]["content"]
                    
                    res_content = await loop.run_in_executor(None, call_api)
                    break  # Success
                except urllib.error.HTTPError as e:
                    if e.code == 429:
                        wait_time = 5.0 * (attempt + 1)
                        state.agent_logs.append(f"Rate limited (429). Retrying in {wait_time}s (Attempt {attempt + 1}/{max_retries})...")
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        state.agent_logs.append(f"Error calling OpenRouter: HTTP Error {e.code}: {e.reason}")
                        break
                except Exception as e:
                    state.agent_logs.append(f"Error calling OpenRouter: {e}")
                    break
            
            if res_content is None and attempt == max_retries - 1:
                state.agent_logs.append("Error: Exceeded maximum retries for OpenRouter API.")
                break
                
            if not res_content:
                state.agent_logs.append("Error: Received empty response from OpenRouter.")
                break
                
            try:
                content_str = res_content.strip()
                if content_str.startswith("```"):
                    lines = content_str.splitlines()
                    if lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].startswith("```"):
                        lines = lines[:-1]
                    content_str = "\n".join(lines).strip()
                decision = json.loads(content_str)
            except Exception as e:
                state.agent_logs.append(f"Error parsing VLM response: {e}")
                state.agent_logs.append(f"Raw VLM response content: {res_content}")
                break
                
            thought = decision.get("thought", "")
            action = decision.get("action")
            
            state.agent_logs.append(f"AI Thought: {thought}")
            state.agent_logs.append(f"AI Decision: {action} ({ {k:v for k,v in decision.items() if k not in ['thought', 'action']} })")
            
            if action == "finish":
                state.agent_logs.append(f"Goal completed! Message: {decision.get('message', '')}")
                break
                
            # Execute step
            args = {k: v for k, v in decision.items() if k not in ["thought", "action"]}
            
            try:
                if action == "sleep":
                    duration = int(args.get("duration", 1000))
                    await asyncio.sleep(duration / 1000.0)
                else:
                    await execute_action_cb(decision, False)
            except Exception as e:
                state.agent_logs.append(f"Error executing action: {e}")
                break
                
            history.append(f"{action} ({args}) - {thought}")
            await asyncio.sleep(3.0)
            
        else:
            state.agent_logs.append("Reached maximum step limit.")
            
    except asyncio.CancelledError:
        state.agent_logs.append("Agent task was cancelled.")
    except Exception as e:
        state.agent_logs.append(f"Unexpected agent error: {e}")
    finally:
        state.agent_running = False
        state.agent_task = None
        state.agent_logs.append("Agent execution finished.")
