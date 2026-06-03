중단된 프롬프트 러너를 재개합니다. state.json에 저장된 마지막 위치부터 이어서 실행합니다.

!python3 "$CLAUDE_PROJECT_DIR/prompt-runner/run.py" \
  --resume \
  --project-dir "$CLAUDE_PROJECT_DIR" \
  --max-turns 0 \
  --timeout 0 \
  --idle-timeout 0 \
  --delay 60
