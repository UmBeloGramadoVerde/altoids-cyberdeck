#!/usr/bin/env bash

if [[ -f /etc/bash.bashrc ]]; then
  source /etc/bash.bashrc
fi

if [[ -f "$HOME/.bashrc" ]]; then
  source "$HOME/.bashrc"
fi

export ALTOIDS_CYBERDECK=1
export CLICOLOR=1
export PATH="/opt/altoids/runtime/bin:$PATH"

if [[ -x /opt/altoids/runtime/bin/cdx ]]; then
  alias cdx='/opt/altoids/runtime/bin/cdx'
fi

_altoids_short_pwd() {
  local cwd="${PWD/#$HOME/~}"
  local IFS='/'
  read -r -a parts <<< "${cwd#/}"

  if [[ "$cwd" == "~" || "$cwd" == "/" ]]; then
    printf '%s' "$cwd"
    return
  fi

  if (( ${#parts[@]} <= 2 )); then
    printf '%s' "$cwd"
    return
  fi

  if [[ "$cwd" == ~/* ]]; then
    printf '~/%s/%s' "${parts[-2]}" "${parts[-1]}"
    return
  fi

  printf '%s/%s' "${parts[-2]}" "${parts[-1]}"
}

_altoids_prompt() {
  local exit_code=$?
  local path_label="$(_altoids_short_pwd)"
  local status=""
  local title="\[\e]2;${path_label}\a\]"
  if [[ $exit_code -ne 0 ]]; then
    status="\[\e[31m\]!${exit_code} \[\e[0m\]"
  fi
  PS1="${title}${status}\[\e[32m\]${path_label}\[\e[0m\]\\$ "
}

_altoids_prev_prompt_command="${PROMPT_COMMAND:-}"

_altoids_run_prompt_command() {
  local exit_code=$?
  if [[ -n "$_altoids_prev_prompt_command" ]]; then
    eval "$_altoids_prev_prompt_command"
  fi
  return "$exit_code"
}

PROMPT_COMMAND="_altoids_run_prompt_command; _altoids_prompt"
