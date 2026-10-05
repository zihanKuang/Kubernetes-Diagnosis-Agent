#!/usr/bin/env python3
"""
Main CLI entry point for ReAct Agent
"""
import asyncio
import argparse
import sys
from pathlib import Path

from .agent import ReActAgent
from .config import AgentConfig


async def main():
    """Main CLI function"""
    parser = argparse.ArgumentParser(
        description="ReAct Agent CLI - Kubernetes Operations Assistant"
    )
    
    parser.add_argument(
        "query",
        nargs="?",
        help="Query to ask the agent"
    )
    
    parser.add_argument(
        "--model",
        default=None,
        help="LLM model (default: MODEL_NAME env or deepseek-v4-flash)",
    )
    
    parser.add_argument(
        "--max-steps",
        type=int,
        default=10,
        help="Maximum reasoning steps (default: 10)"
    )
    
    parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="Start interactive mode"
    )
    
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose output (DEBUG level)"
    )

    parser.add_argument(
        "--mcp-url",
        default=None,
        help="Streamable HTTP MCP endpoint (default: local stdio)",
    )

    parser.add_argument(
        "--engine",
        choices=["react", "langgraph"],
        default="react",
        help="Orchestration engine: hand-written ReAct loop (default) or "
             "LangGraph StateGraph (same tools/harness, needs the langgraph extra)",
    )
    
    args = parser.parse_args()
    
    # Create config
    config = AgentConfig(
        max_steps=args.max_steps,
        log_level="DEBUG" if args.verbose else "INFO",
        mcp_url=args.mcp_url,
    )
    if args.model:
        config.model_name = args.model

    if not config.api_key:
        print("Error: no LLM API key configured")
        print("\nSet it with:")
        print("  copy agent_cli\\.env.example agent_cli\\.env")
        print("  then put your key in DEEPSEEK_API_KEY= (hosted default)")
        print("  or point at a self-hosted server via CITRUS_LLM_PROVIDER=vllm")
        print("  + CITRUS_LLM_BASE_URL= + CITRUS_LLM_MODEL=")
        sys.exit(1)
    
    # Create and initialize agent
    if args.engine == "langgraph":
        try:
            from .engine_langgraph import LangGraphAgent
            agent = LangGraphAgent(config)
        except ImportError as e:
            print(f"Error: {e}")
            sys.exit(1)
    else:
        agent = ReActAgent(config)
    
    try:
        await agent.initialize()
        
        if args.interactive:
            # Interactive mode
            await interactive_mode(agent)
        elif args.query:
            # Single query mode
            result = await agent.run(args.query)
            print(f"\n{result}\n")
        else:
            # No query provided
            parser.print_help()
            sys.exit(1)
    
    finally:
        await agent.cleanup()


async def interactive_mode(agent: ReActAgent):
    """Interactive REPL mode"""
    print("\n" + "="*80)
    print("ReAct Agent - Interactive Mode")
    print("="*80)
    print("Type your queries below. Type 'exit' or 'quit' to exit.\n")
    
    while True:
        try:
            # Get user input
            query = input("You: ").strip()
            
            if not query:
                continue
            
            if query.lower() in ["exit", "quit", "q"]:
                print("\nGoodbye!\n")
                break
            
            # Run agent
            print()
            result = await agent.run(query)
            print(f"\nAgent: {result}\n")
        
        except KeyboardInterrupt:
            print("\n\nInterrupted. Goodbye!\n")
            break
        except EOFError:
            print("\n\nGoodbye!\n")
            break


if __name__ == "__main__":
    asyncio.run(main())
