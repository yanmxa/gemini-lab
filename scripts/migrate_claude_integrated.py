import os
import sys
import argparse
import shutil

# Defaults
DEFAULT_SOURCE_ROOT = os.path.expanduser("~/.claude")
DEFAULT_GLOBAL_TARGET = os.path.expanduser("~/.gemini")
IMPORT_TAG = "# Tags: claude-code-import"

def parse_frontmatter(content):
    """Extract description and other metadata from frontmatter."""
    meta = {"description": "Imported from Claude Code"}
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return meta
    
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line:
            parts = line.split(":", 1)
            if len(parts) == 2:
                key, val = parts
                meta[key.strip()] = val.strip().strip('"\'')
    return meta

def make_toml_content(src_content, meta, original_path, scope):
    """Create TOML content. Embeds content for global scope, uses ref link for project scope."""
    desc = meta.get("description", "Imported command").replace('"', '\"')
    
    if scope == "project":
        # Use reference link for project scope so changes in ~/.claude are reflected
        # Using @path syntax for dynamic inclusion
        prompt_text = f"@{original_path}"
    else:
        # Global scope: Embed content directly for portability/stability
        safe_content = src_content.replace('"""', '\"""')
        prompt_text = safe_content
    
    return f'''description = "{desc}"
{IMPORT_TAG}
# Source: {original_path}

prompt = """
{prompt_text}
"""
'''

def cleanup_phase(target_commands_dir):
    """Phase 0: Cleanup previously imported files."""
    print(f"Phase 0: Cleaning up previously imported files in {target_commands_dir}...")
    if not os.path.exists(target_commands_dir):
        return

    count = 0
    # Protected files that should never be deleted by the migration cleanup
    protected_files = {"cc-command.toml"}

    for root, _, files in os.walk(target_commands_dir):
        for file in files:
            if file.endswith(".toml"):
                if file in protected_files:
                    continue
                    
                path = os.path.join(root, file)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        if IMPORT_TAG in f.read():
                            os.remove(path)
                            count += 1
                except Exception as e:
                    print(f"Error checking {path}: {e}")
    print(f"Removed {count} previously imported files.")

def migrate_file(src_path, category, name, target_commands_dir, strategy, scope):
    try:
        with open(src_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        print(f"Skipping {src_path}: {e}")
        return

    meta = parse_frontmatter(content)
    target_dir = os.path.join(target_commands_dir, category)
    target_name = name.replace(".md", ".toml")
    target_path = os.path.join(target_dir, target_name)
    
    exists = os.path.exists(target_path)
    
    if exists:
        if strategy in ["ignore", "auto"]:
            return
        # 'force' and 'override' both overwrite
    
    os.makedirs(target_dir, exist_ok=True)
    toml_data = make_toml_content(content, meta, src_path, scope)
    
    with open(target_path, "w", encoding="utf-8") as f:
        f.write(toml_data)
    print(f"Migrated: {category}/{target_name}")

def main():
    parser = argparse.ArgumentParser(description="Migrate Claude Code to Gemini")
    parser.add_argument("target", nargs="?", default="all", choices=["all", "commands", "plugins"],
                        help="What to migrate (default: all)")
    parser.add_argument("--scope", choices=["global", "project"], default="global", 
                        help="Migration scope (default: global)")
    parser.add_argument("--strategy", choices=["force", "override", "ignore", "auto", "delete"], default="auto",
                        help="Migration strategy (default: auto)")
    
    args = parser.parse_args()
    
    source_root = DEFAULT_SOURCE_ROOT
    if args.scope == "global":
        target_root = DEFAULT_GLOBAL_TARGET
    else:
        target_root = os.path.join(os.getcwd(), ".gemini")
    
    target_commands = os.path.join(target_root, "commands")
    
    # Handle 'delete' strategy: Clean up and exit
    if args.strategy == "delete":
        print(f"Target scope: {args.scope} ({target_root})")
        print("Strategy: delete (Removing previously migrated files)")
        cleanup_phase(target_commands)
        return

    if not os.path.exists(source_root):
        print(f"Source {source_root} not found. Nothing to migrate.")
        return

    print(f"Target scope: {args.scope} ({target_root})")
    print(f"Strategy: {args.strategy}")
    print(f"Migrating: {args.target}")

    # Phase 0: Cleanup
    if args.strategy == "force":
        cleanup_phase(target_commands)

    # Migrate Commands
    if args.target in ["all", "commands"]:
        user_cmds_root = os.path.join(source_root, "commands")
        if os.path.exists(user_cmds_root):
            for root, _, files in os.walk(user_cmds_root):
                for file in files:
                    if file.endswith(".md"):
                        abs_path = os.path.join(root, file)
                        rel_path = os.path.relpath(root, user_cmds_root)
                        category = "user_misc" if rel_path == "." else rel_path
                        migrate_file(abs_path, category, file, target_commands, args.strategy, args.scope)

    # Migrate Plugins
    if args.target in ["all", "plugins"]:
        plugins_cache_root = os.path.join(source_root, "plugins", "cache")
        if os.path.exists(plugins_cache_root):
            for root, _, files in os.walk(plugins_cache_root):
                if os.path.basename(root) == "commands":
                    parts = root.split(os.sep)
                    try:
                        cache_idx = parts.index("cache")
                        plugin_name = parts[cache_idx + 2] if len(parts) > cache_idx + 2 else os.path.basename(os.path.dirname(root))
                    except (ValueError, IndexError):
                        plugin_name = os.path.basename(os.path.dirname(root))
                    
                    for file in files:
                        if file.endswith(".md"):
                            migrate_file(os.path.join(root, file), plugin_name, file, target_commands, args.strategy, args.scope)

if __name__ == "__main__":
    main()
