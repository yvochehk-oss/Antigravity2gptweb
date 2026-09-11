with open('start_all.sh', 'r') as f:
    content = f.read()

old_block = """if [ -f "$PROJECT_DIR/models/local-llm/Qwen3.5-2B-Q4_K_M.gguf" ]; then
  DEFAULT_LOCAL_MODEL="$PROJECT_DIR/models/local-llm/Qwen3.5-2B-Q4_K_M.gguf"
  DEFAULT_LOCAL_ALIAS="local-qwen3.5-2b"
elif [ -f "$PROJECT_DIR/models/local-llm/Ling-3.0-tiny-Q4_K_M.gguf" ]; then
  DEFAULT_LOCAL_MODEL="$PROJECT_DIR/models/local-llm/Ling-3.0-tiny-Q4_K_M.gguf"
  DEFAULT_LOCAL_ALIAS="ling-3.0-tiny"
fi"""

new_block = """if [ -f "$PROJECT_DIR/models/local-llm/Ling-3.0-tiny-Q4_K_M.gguf" ]; then
  DEFAULT_LOCAL_MODEL="$PROJECT_DIR/models/local-llm/Ling-3.0-tiny-Q4_K_M.gguf"
  DEFAULT_LOCAL_ALIAS="ling-3.0-tiny"
elif [ -f "$PROJECT_DIR/models/local-llm/Qwen3.5-2B-Q4_K_M.gguf" ]; then
  DEFAULT_LOCAL_MODEL="$PROJECT_DIR/models/local-llm/Qwen3.5-2B-Q4_K_M.gguf"
  DEFAULT_LOCAL_ALIAS="local-qwen3.5-2b"
fi"""

content = content.replace(old_block, new_block)

with open('start_all.sh', 'w') as f:
    f.write(content)
