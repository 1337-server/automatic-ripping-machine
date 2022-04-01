#!/bin/bash

# set the location of the git hooks for this repo only
git config --local core.hooksPath ".git/hooks"

# symlink all the provided links to make them usable
ln -sf .githooks/* .git/hooks/