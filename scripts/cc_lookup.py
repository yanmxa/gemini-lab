#!/usr/bin/env python3
"""
Claude Code Command Lookup Script

Searches for Claude Code commands across multiple locations:
1. Project commands: ./.claude/commands/
2. User commands: ~/.claude/commands/
3. Enabled plugin commands: from installed_plugins.json cache paths

Also resolves referenced scripts and skills for complete command analysis.
"""

import os
import json
import sys
import re
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple


def load_json(path: Path) -> Dict:
    """Load JSON file, return empty dict on error."""
    try:
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def read_file(path: Path) -> Optional[str]:
    """Read file content, return None on error."""
    try:
        if path.exists():
            return path.read_text(encoding='utf-8')
    except Exception:
        pass
    return None


def normalize_command_name(cmd_name: str) -> List[str]:
    """
    Normalize command name to possible file paths.
    Handles formats like: 'jira:my-issues', 'jira/my-issues', 'my-issues'
    Returns list of possible paths to check.
    """
    candidates = []

    # Handle colon-separated names (jira:my-issues -> jira/my-issues.md)
    if ':' in cmd_name:
        parts = cmd_name.split(':')
        candidates.append('/'.join(parts) + '.md')

    # Handle slash-separated names
    if '/' in cmd_name:
        candidates.append(cmd_name + '.md')

    # Direct name
    candidates.append(cmd_name + '.md')

    # Also try without extension if already has .md
    if cmd_name.endswith('.md'):
        candidates.append(cmd_name)

    return list(dict.fromkeys(candidates))  # Remove duplicates while preserving order


def get_enabled_plugins(home: Path) -> List[str]:
    """Get list of enabled plugin IDs from settings files."""
    settings_paths = [
        Path("./.claude/settings.local.json"),
        Path("./.claude/settings.json"),
        home / ".claude/settings.local.json",
        home / ".claude/settings.json"
    ]

    enabled_plugins = []

    for s_path in settings_paths:
        data = load_json(s_path)
        plugins = data.get("enabledPlugins", {})
        for p_id, enabled in plugins.items():
            if enabled and p_id not in enabled_plugins:
                enabled_plugins.append(p_id)

    return enabled_plugins


def get_installed_plugin_paths(home: Path) -> Dict[str, str]:
    """
    Get mapping of plugin ID to install path from installed_plugins.json.

    Structure:
    {
      "version": 2,
      "plugins": {
        "plugin-id@marketplace": [
          {
            "scope": "user",
            "installPath": "/path/to/plugin",
            ...
          }
        ]
      }
    }
    """
    installed_path = home / ".claude/plugins/installed_plugins.json"
    data = load_json(installed_path)

    plugin_paths = {}
    plugins = data.get("plugins", {})

    for plugin_id, installations in plugins.items():
        if isinstance(installations, list) and len(installations) > 0:
            # Take the first (most recent) installation
            install_info = installations[0]
            if isinstance(install_info, dict) and "installPath" in install_info:
                install_path = install_info["installPath"]

                # Verify path exists, if not try to find alternative version
                if Path(install_path).exists():
                    plugin_paths[plugin_id] = install_path
                else:
                    # Try to find an existing version in the cache
                    alternative = find_alternative_plugin_version(install_path)
                    if alternative:
                        plugin_paths[plugin_id] = alternative

    return plugin_paths


def find_alternative_plugin_version(install_path: str) -> Optional[str]:
    """
    If the specified install path doesn't exist, try to find an alternative
    version of the plugin in the same cache directory.

    E.g., if /path/to/plugin/1.2.0 doesn't exist but /path/to/plugin/1.1.4 does,
    return the latter.
    """
    path = Path(install_path)
    parent = path.parent  # The plugin directory (e.g., .../git/)

    if not parent.exists():
        return None

    # List all version directories
    versions = []
    for item in parent.iterdir():
        if item.is_dir() and not item.name.startswith('.'):
            versions.append(item)

    if not versions:
        return None

    # Sort by modification time (most recent first) or name
    versions.sort(key=lambda x: x.stat().st_mtime, reverse=True)

    # Return the most recently modified version
    return str(versions[0])


def find_command_in_directory(base_path: Path, cmd_candidates: List[str]) -> Optional[Path]:
    """Try to find command file in a directory using candidate paths."""
    for candidate in cmd_candidates:
        cmd_path = base_path / candidate
        if cmd_path.exists() and cmd_path.is_file():
            return cmd_path
    return None


def list_available_commands(base_path: Path, prefix: str = "") -> List[str]:
    """List all available command files in a directory."""
    commands = []
    if not base_path.exists():
        return commands

    for item in base_path.rglob("*.md"):
        relative = item.relative_to(base_path)
        # Convert path to command name format
        cmd_name = str(relative)[:-3]  # Remove .md
        if prefix:
            cmd_name = f"{prefix}:{cmd_name}"
        commands.append(cmd_name.replace('/', ':'))

    return commands


def parse_command_content(content: str) -> Dict[str, Any]:
    """
    Parse Claude Code command markdown file.
    Extract frontmatter and body content.
    """
    result = {
        "frontmatter": {},
        "body": content,
        "description": "",
        "allowed_tools": [],
        "argument_hint": ""
    }

    # Check for YAML frontmatter
    if content.startswith('---'):
        parts = content.split('---', 2)
        if len(parts) >= 3:
            frontmatter_text = parts[1].strip()
            result["body"] = parts[2].strip()

            # Simple YAML parsing for common fields
            for line in frontmatter_text.split('\n'):
                if ':' in line:
                    key, value = line.split(':', 1)
                    key = key.strip()
                    value = value.strip()

                    # Handle arrays like [Bash, Read]
                    if value.startswith('[') and value.endswith(']'):
                        value = [v.strip() for v in value[1:-1].split(',')]

                    result["frontmatter"][key] = value

            result["description"] = result["frontmatter"].get("description", "")
            result["allowed_tools"] = result["frontmatter"].get("allowed-tools", [])
            result["argument_hint"] = result["frontmatter"].get("argument-hint", "")

    return result


def extract_script_references(content: str) -> List[str]:
    """Extract script file references from command content."""
    scripts = []

    # Match patterns like ~/.claude/scripts/something.sh or ~/.claude/scripts/something.py
    patterns = [
        r'~/.claude/scripts/[\w\-]+\.\w+',
        r'\$HOME/.claude/scripts/[\w\-]+\.\w+',
        r'/Users/\w+/.claude/scripts/[\w\-]+\.\w+',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, content)
        scripts.extend(matches)

    return list(set(scripts))


def extract_skill_references(content: str) -> List[str]:
    """Extract skill references from command content (e.g., /skill-name)."""
    # Match patterns like: uses the /skill-name skill
    skills = []
    pattern = r'/([a-z][\w\-:]+)'
    matches = re.findall(pattern, content.lower())

    # Filter out common false positives
    false_positives = {'dev', 'null', 'tmp', 'bin', 'usr', 'etc', 'var', 'home'}
    skills = [m for m in matches if m not in false_positives and ':' in m or len(m) > 3]

    return list(set(skills))


def resolve_script_content(script_path: str, home: Path) -> Optional[Dict[str, str]]:
    """Resolve and read a script file."""
    # Expand ~ to home directory
    resolved = script_path.replace('~', str(home)).replace('$HOME', str(home))
    path = Path(resolved)

    content = read_file(path)
    if content:
        return {
            "path": str(path),
            "content": content
        }
    return None


def find_command(cmd_name: str) -> Dict[str, Any]:
    """
    Main function to find a Claude Code command.

    Search order:
    1. ./.claude/commands/ (project level)
    2. ~/.claude/commands/ (user level)
    3. Enabled plugins from settings -> installed_plugins.json cache paths

    Returns detailed information about the command if found.
    """
    home = Path.home()
    cmd_candidates = normalize_command_name(cmd_name)

    result = {
        "found": False,
        "search_query": cmd_name,
        "candidates_checked": cmd_candidates,
        "locations_searched": []
    }

    # 1. Project commands
    project_cmd_dir = Path("./.claude/commands")
    result["locations_searched"].append(str(project_cmd_dir.absolute()))
    found_path = find_command_in_directory(project_cmd_dir, cmd_candidates)
    if found_path:
        return build_result(found_path, "project", result, home)

    # 2. User commands
    user_cmd_dir = home / ".claude/commands"
    result["locations_searched"].append(str(user_cmd_dir))
    found_path = find_command_in_directory(user_cmd_dir, cmd_candidates)
    if found_path:
        return build_result(found_path, "user", result, home)

    # 3. Plugin commands
    enabled_plugins = get_enabled_plugins(home)
    installed_paths = get_installed_plugin_paths(home)

    for plugin_id in enabled_plugins:
        if plugin_id in installed_paths:
            plugin_path = Path(installed_paths[plugin_id])
            plugin_cmd_dir = plugin_path / "commands"
            result["locations_searched"].append(str(plugin_cmd_dir))

            found_path = find_command_in_directory(plugin_cmd_dir, cmd_candidates)
            if found_path:
                return build_result(found_path, f"plugin:{plugin_id}", result, home)

    # Not found - collect available commands for suggestions
    result["available_commands"] = collect_available_commands(home, enabled_plugins, installed_paths)

    return result


def build_result(found_path: Path, source: str, base_result: Dict, home: Path) -> Dict[str, Any]:
    """Build complete result with command content and references."""
    content = read_file(found_path)
    if not content:
        base_result["error"] = f"Found command at {found_path} but failed to read content"
        return base_result

    parsed = parse_command_content(content)
    scripts = extract_script_references(content)
    skills = extract_skill_references(content)

    # Resolve script contents
    resolved_scripts = {}
    for script in scripts:
        script_data = resolve_script_content(script, home)
        if script_data:
            resolved_scripts[script] = script_data

    return {
        "found": True,
        "path": str(found_path.absolute()),
        "source": source,
        "content": content,
        "parsed": parsed,
        "referenced_scripts": resolved_scripts,
        "referenced_skills": skills,
        "locations_searched": base_result["locations_searched"]
    }


def collect_available_commands(home: Path, enabled_plugins: List[str], installed_paths: Dict[str, str]) -> List[Dict[str, str]]:
    """Collect all available commands for suggestions."""
    available = []

    # Project commands
    project_cmds = list_available_commands(Path("./.claude/commands"))
    for cmd in project_cmds:
        available.append({"name": cmd, "source": "project"})

    # User commands
    user_cmds = list_available_commands(home / ".claude/commands")
    for cmd in user_cmds:
        available.append({"name": cmd, "source": "user"})

    # Plugin commands
    for plugin_id in enabled_plugins:
        if plugin_id in installed_paths:
            plugin_path = Path(installed_paths[plugin_id])
            plugin_name = plugin_id.split('@')[0]
            plugin_cmds = list_available_commands(plugin_path / "commands", plugin_name)
            for cmd in plugin_cmds:
                available.append({"name": cmd, "source": f"plugin:{plugin_id}"})

    return available


def main():
    if len(sys.argv) < 2:
        # No argument - list all available commands
        home = Path.home()
        enabled_plugins = get_enabled_plugins(home)
        installed_paths = get_installed_plugin_paths(home)
        available = collect_available_commands(home, enabled_plugins, installed_paths)
        print(json.dumps({
            "mode": "list",
            "available_commands": available
        }, indent=2))
        return

    cmd_name = sys.argv[1]

    # Handle special --list flag
    if cmd_name == "--list":
        home = Path.home()
        enabled_plugins = get_enabled_plugins(home)
        installed_paths = get_installed_plugin_paths(home)
        available = collect_available_commands(home, enabled_plugins, installed_paths)
        print(json.dumps({
            "mode": "list",
            "available_commands": available
        }, indent=2))
        return

    result = find_command(cmd_name)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
