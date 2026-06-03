프롬프트 러너를 실행합니다. 001번부터 순서대로 모든 프롬프트 블럭을 자동 실행합니다.

!python3 "$CLAUDE_PROJECT_DIR/prompt-runner/run.py" \
  --project-dir "$CLAUDE_PROJECT_DIR" \
  --max-turns 0 \
  --timeout 0 \
  --idle-timeout 0 \
  --delay 60
