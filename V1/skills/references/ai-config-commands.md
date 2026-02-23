# AI Config Commands

Use this file only when detailed flags are needed.

## Claude Code (non-interactive)

```bash
python skills/scripts/ai/claude_code_config_cli.py \
  --api-key "$ZAI_API_KEY" \
  --base-url "https://open.bigmodel.cn/api/anthropic" \
  --model "default" \
  --timeout-ms 3000000
```

## Claude Code tool (original powerful CLI)

```bash
python skills/scripts/ai/cc_config_tool.py --help
python skills/scripts/ai/cc_config_tool.py --preset openrouter --key sk-xxxx
python skills/scripts/ai/cc_config_tool.py --list
```

## Codex config

```bash
python skills/scripts/ai/codex_config_tool.py --help
python skills/scripts/ai/codex_config_tool.py --list
```

## Crush config

```bash
python skills/scripts/ai/crush-config.py --help
python skills/scripts/ai/crush-config.py list-providers
```

## OpenCode config

```bash
python skills/scripts/ai/opencode-config.py --help
python skills/scripts/ai/opencode-config.py list-providers
```
