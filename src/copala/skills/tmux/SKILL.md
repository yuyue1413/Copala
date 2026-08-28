---
name: tmux
description: 通过发送按键和抓取面板输出来远程控制 tmux 会话，用于需要交互式 TTY 的场景。
metadata: {"copala":{"emoji":"🧵","os":["darwin","linux"],"requires":{"bins":["tmux"]}}}
---

# tmux 技能

仅在需要交互式 TTY 时使用 tmux。长时间运行的非交互任务优先使用 exec 后台模式。

## 快速开始（隔离 socket，exec 工具）

```bash
SOCKET_DIR="${COPALA_TMUX_SOCKET_DIR:-${TMPDIR:-/tmp}/copala-tmux-sockets}"
mkdir -p "$SOCKET_DIR"
SOCKET="$SOCKET_DIR/copala.sock"
SESSION=copala-python

tmux -S "$SOCKET" new -d -s "$SESSION" -n shell
tmux -S "$SOCKET" send-keys -t "$SESSION":0.0 -- 'PYTHON_BASIC_REPL=1 python3 -q' Enter
tmux -S "$SOCKET" capture-pane -p -J -t "$SESSION":0.0 -S -200
```

启动会话后，始终打印监控命令：

```
To monitor:
  tmux -S "$SOCKET" attach -t "$SESSION"
  tmux -S "$SOCKET" capture-pane -p -J -t "$SESSION":0.0 -S -200
```

## Socket 约定

- 使用 `COPALA_TMUX_SOCKET_DIR` 环境变量。
- 默认 socket 路径：`"$COPALA_TMUX_SOCKET_DIR/copala.sock"`。

## 定位面板与命名

- 目标格式：`session:window.pane`（默认 `:0.0`）。
- 名称保持简短，避免空格。
- 查看：`tmux -S "$SOCKET" list-sessions`，`tmux -S "$SOCKET" list-panes -a`。

## 查找会话

- 列出指定 socket 上的会话：`{baseDir}/scripts/find-sessions.sh -S "$SOCKET"`。
- 扫描所有 socket：`{baseDir}/scripts/find-sessions.sh --all`（使用 `COPALA_TMUX_SOCKET_DIR`）。

## 安全发送输入

- 优先使用字面发送：`tmux -S "$SOCKET" send-keys -t target -l -- "$cmd"`。
- 控制键：`tmux -S "$SOCKET" send-keys -t target C-c`。

## 监控输出

- 抓取最近历史：`tmux -S "$SOCKET" capture-pane -p -J -t target -S -200`。
- 等待提示符：`{baseDir}/scripts/wait-for-text.sh -t session:0.0 -p 'pattern'`。
- 可以直接 attach；用 `Ctrl+b d` 分离。

## 启动进程

- Python REPL 需设置 `PYTHON_BASIC_REPL=1`（非 basic REPL 会导致 send-keys 流程异常）。

## Windows / WSL

- tmux 仅支持 macOS/Linux。在 Windows 上请使用 WSL 并在 WSL 内安装 tmux。
- 此技能仅对 `darwin`/`linux` 生效，且要求 PATH 中有 `tmux`。

## 编排多个 Coding Agent（Codex、Claude Code）

tmux 非常适合并行运行多个 coding agent：

```bash
SOCKET="${TMPDIR:-/tmp}/codex-army.sock"

# 创建多个会话
for i in 1 2 3 4 5; do
  tmux -S "$SOCKET" new-session -d -s "agent-$i"
done

# 在不同工作目录启动 agent
tmux -S "$SOCKET" send-keys -t agent-1 "cd /tmp/project1 && codex --yolo 'Fix bug X'" Enter
tmux -S "$SOCKET" send-keys -t agent-2 "cd /tmp/project2 && codex --yolo 'Fix bug Y'" Enter

# 轮询完成状态（检测提示符是否返回）
for sess in agent-1 agent-2; do
  if tmux -S "$SOCKET" capture-pane -p -t "$sess" -S -3 | grep -q "❯"; then
    echo "$sess: DONE"
  else
    echo "$sess: Running..."
  fi
done

# 获取已完成会话的完整输出
tmux -S "$SOCKET" capture-pane -p -t agent-1 -S -500
```

**技巧：**
- 为并行修复使用独立 git worktree（避免分支冲突）
- 在新克隆的仓库中先运行 `pnpm install`
- 检测 `❯` 或 `$` 来判断任务完成
- Codex 需加 `--yolo` 或 `--full-auto` 才能非交互运行

## 清理

```bash
# 关闭单个会话
tmux -S "$SOCKET" kill-session -t "$SESSION"
# 关闭 socket 上的所有会话
tmux -S "$SOCKET" list-sessions -F '#{session_name}' | xargs -r -n1 tmux -S "$SOCKET" kill-session -t
# 销毁整个私有 socket
tmux -S "$SOCKET" kill-server
```

## 辅助脚本：wait-for-text.sh

`{baseDir}/scripts/wait-for-text.sh` 轮询面板，等待匹配正则（或固定字符串），支持超时。

```bash
{baseDir}/scripts/wait-for-text.sh -t session:0.0 -p 'pattern' [-F] [-T 20] [-i 0.5] [-l 2000]
```

| 参数 | 说明 |
|------|------|
| `-t`/`--target` | 面板目标（必填） |
| `-p`/`--pattern` | 匹配正则（必填）；加 `-F` 为固定字符串匹配 |
| `-T` | 超时秒数（整数，默认 15） |
| `-i` | 轮询间隔秒数（默认 0.5） |
| `-l` | 搜索的历史行数（整数，默认 1000） |
