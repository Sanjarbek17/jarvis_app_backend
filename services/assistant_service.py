import json
import base64
import asyncio
import urllib.request
import urllib.error
from typing import Callable, Awaitable
from core import state
from core.config import AI_PROVIDER
from services.llm_providers import get_llm_provider

SYSTEM_PROMPT = """You are an AI phone controller. You look at a phone screen, read the user's goal, and output the single next action to take.

AVAILABLE ACTIONS:
Choose ONLY ONE action from this list.

1. Open App: {"action": "open", "text": "App Name"}
2. Click Text/Button: {"action": "click", "label": "Exact Text"} 
3. Tap Coordinates: {"action": "tap", "x": 0.5, "y": 0.5} (Must use normalized coordinates 0.0 to 1.0)
4. Swipe: {"action": "swipe", "x": 0.5, "y": 0.8, "x2": 0.5, "y2": 0.2, "duration": 500}
5. Type Text: {"action": "write", "text": "words to type"}
6. Go Back: {"action": "back"}
7. Go Home: {"action": "home"}
8. Wait: {"action": "sleep", "duration": 1000} (Duration in milliseconds)
9. End Task: {"action": "finish", "message": "Task complete or failed"}

STRICT RULES:
1. OUTPUT FORMAT: You must output ONLY a valid JSON object containing a "thought" string and the chosen action fields. No extra text.
2. GAMES: "click" and "open" do not work in games or custom UIs. You MUST use "tap" or "swipe".
3. STUCK LOOPS: Look at the history. If you previously tried "click" or "open" and the screen did not change, you are stuck. You MUST switch to using "tap" with exact x/y coordinates.
4. FIXING MISTAKES (CRITICAL): If your last action was a "tap" and the screen did not change, your coordinates were WRONG. You MUST NOT use the exact same coordinates again. Adjust the "x" or "y" values by at least 0.1 or 0.2 to try a new location.

EXAMPLE OUTPUT:
{
  "thought": "My previous tap at x: 0.5, y: 0.2 did not work. I will adjust the y coordinate down slightly to try and hit the button.",
  "action": "tap",
  "x": 0.5,
  "y": 0.3
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
            
            state.agent_logs.append(f"Step {step_num}: Querying AI model using '{AI_PROVIDER}' provider...")
            
            prompt = f"Goal: {goal}\nSteps taken so far:\n"
            for idx, step in enumerate(history):
                prompt += f"{idx+1}. {step}\n"
            prompt += "\nDecide the next action based on the screenshot."
            
            provider = get_llm_provider(AI_PROVIDER)
            res_content = await provider.query_model(SYSTEM_PROMPT, prompt, b64_image)

            if not res_content:
                state.agent_logs.append("Error: Received empty response from the AI provider.")
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
                
                # Repair: gemma3 sometimes puts "click, label: X" as the action value
                action_val = decision.get("action", "")
                if isinstance(action_val, str) and ", " in action_val and ":" in action_val:
                    # Parse "click, label: Saved Messages" -> action=click, label=Saved Messages
                    parts = action_val.split(", ", 1)
                    decision["action"] = parts[0].strip()
                    if ":" in parts[1]:
                        key, val = parts[1].split(":", 1)
                        decision[key.strip()] = val.strip()
                        
            except Exception as e:
                state.agent_logs.append(f"Error parsing VLM response: {e}")
                state.agent_logs.append(f"Raw VLM response content: {res_content}")
                # Try one more time with relaxed parsing
                try:
                    import re
                    # Find the first { ... } block
                    match = re.search(r'\{[^{}]*\}', res_content, re.DOTALL)
                    if match:
                        decision = json.loads(match.group())
                        state.agent_logs.append("Recovered JSON from raw response.")
                    else:
                        break
                except:
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
