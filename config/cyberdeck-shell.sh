#!/usr/bin/env bash

if [[ -f /etc/bash.bashrc ]]; then
  source /etc/bash.bashrc
fi

if [[ -f "$HOME/.bashrc" ]]; then
  source "$HOME/.bashrc"
fi

export ALTOIDS_CYBERDECK=1
export CLICOLOR=1

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

_altoids_set_title() {
  local label="$(_altoids_short_pwd)"
  printf '\033]2;%s\033\\' "$label"
}

_altoids_prompt() {
  local exit_code=$?
  local path_label="$(_altoids_short_pwd)"
  local status=""
  if [[ $exit_code -ne 0 ]]; then
    status="\[\e[31m\]!${exit_code} \[\e[0m\]"
  fi
  PS1="${status}\[\e[32m\]${path_label}\[\e[0m\]\\$ "
}

PROMPT_COMMAND="_altoids_set_title;_altoids_prompt"
