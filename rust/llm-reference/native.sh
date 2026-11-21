#!/bin/sh
exec python3 "$(dirname "$0")/driver.py" --native "$(dirname "$0")/../llm/target/debug/characterize"
