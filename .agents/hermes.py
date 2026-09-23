"""Hermes agent harness running against self-hosted Qwen 27B with full MLflow span tracking."""

import os
import subprocess
import json
import logging
from typing import Any

import mlflow
from litellm import completion

# Basic logging setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("hermes-harness")

SYSTEM_PROMPT = """You are Hermes, an autonomous AI harness engineer.
You are tasked with diagnosing, evaluating, and optimizing the RAG pipeline using the deepfix CLI.
A plugin manifest exists at plugins/deepfix-rag/plugin.yaml which points to skills in .agents/skills/.
Use the run_shell_command tool to execute deepfix commands and read files.
"""

@mlflow.trace(name="execute_shell_command", span_type="TOOL")
def run_shell_command(command: str) -> dict[str, Any]:
    """Execute shell commands via subprocess calling deepfix-rag."""
    logger.info(f"Executing command: {command}")
    
    # Capture tool call payloads in spans
    span = mlflow.get_current_active_span()
    if span:
        span.set_attribute("command", command)
        
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            check=False,
            timeout=120
        )
        payload = {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode
        }
    except Exception as e:
        payload = {
            "stdout": "",
            "stderr": str(e),
            "exit_code": -1
        }
    
    # Store outputs as trace attributes to fulfill observability criteria
    if span:
        span.set_attribute("stdout", payload["stdout"])
        span.set_attribute("stderr", payload["stderr"])
        span.set_attribute("exit_code", payload["exit_code"])
        
    return payload


@mlflow.trace(name="hermes_reasoning_step", span_type="LLM")
def call_llm(messages: list[dict[str, str]]) -> Any:
    """Call the self-hosted Qwen 3.8 27B model on HPI VM."""
    
    model = os.getenv("HERMES_MODEL", "openai/qwen-2.5-27b") # Adjust to HPI Qwen identifier
    api_base = os.getenv("HERMES_API_BASE", "http://hpi-vm.internal:8000/v1")
    api_key = os.getenv("HERMES_API_KEY", "dummy-key")
    
    # Providing the deepfix-rag execution tool to Hermes
    tools = [
        {
            "type": "function",
            "function": {
                "name": "run_shell_command",
                "description": "Executes a shell command on the host machine. Use this to run deepfix commands or read SKILL.md files.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The exact shell command to run."
                        }
                    },
                    "required": ["command"]
                }
            }
        }
    ]

    response = completion(
        model=model,
        messages=messages,
        api_base=api_base,
        api_key=api_key,
        tools=tools,
        max_tokens=2048,
        temperature=0.1
    )
    return response


@mlflow.trace(name="agent_execution_cycle", span_type="AGENT")
def agent_loop(instruction: str, max_iterations: int = 5):
    """Hermes execution cycle with full MLflow span tracking."""
    
    span = mlflow.get_current_active_span()
    if span:
        span.set_attribute("instruction", instruction)
        span.set_attribute("system_prompt", SYSTEM_PROMPT)
    
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": instruction}
    ]
    
    for i in range(max_iterations):
        logger.info(f"--- Agent Execution Cycle {i+1} ---")
        
        # Sub-span for each step of reasoning
        with mlflow.start_span(name=f"cycle_{i+1}") as cycle_span:
            response = call_llm(messages)
            message = response.choices[0].message
            
            # Record LLM response Payload
            cycle_span.set_attribute("llm_message", message.model_dump_json())
            
            if message.tool_calls:
                messages.append(message.model_dump(exclude_none=True))
                for tool_call in message.tool_calls:
                    if tool_call.function.name == "run_shell_command":
                        args = json.loads(tool_call.function.arguments)
                        cmd = args.get("command", "")
                        
                        # Execute tool (auto-wrapped in trace)
                        result_payload = run_shell_command(cmd)
                        
                        tool_log = (
                            f"Exit Code: {result_payload['exit_code']}\n"
                            f"STDOUT:\n{result_payload['stdout']}\n"
                            f"STDERR:\n{result_payload['stderr']}"
                        )
                        
                        messages.append({
                            "role": "tool",
                            "name": tool_call.function.name,
                            "tool_call_id": tool_call.id,
                            "content": tool_log
                        })
            else:
                final_answer = message.content or ""
                cycle_span.set_attribute("final_answer", final_answer)
                logger.info(f"Agent finished: {final_answer}")
                print(f"\nFinal Answer:\n{final_answer}")
                break

if __name__ == "__main__":
    mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", "sqlite:///mlruns.db"))
    mlflow.set_experiment("hermes_agent_harness")
    
    print("Hermes Agent Harness initialized. Target: Qwen 3.8 27B")
    
    # Execute a sample agent loop kickoff
    user_task = "Diagnose the RAG system performance using deepfix run-diagnostic."
    
    # Outer run context encapsulates all span traces
    with mlflow.start_run(run_name="hermes_session_1"):
        agent_loop(user_task)
