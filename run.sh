#!/bin/bash

pip install -r __site/requirements.txt

# Avoid copying over netlify.toml (will ebe exposed to public API)
echo "netlify.toml" >>__obsidian/.gitignore

# Sync Zola template contents
rsync -a __site/zola/ __site/build
rsync -a __site/content/ __site/build/content

# Use obsidian-export to export markdown content from obsidian
mkdir -p __site/build/__originals
if [ -z "$STRICT_LINE_BREAKS" ]; then
	__site/bin/obsidian-export --hard-linebreaks --no-recursive-embeds __obsidian __site/build/__originals
else
	__site/bin/obsidian-export --no-recursive-embeds __obsidian __site/build/__originals
fi

# Run conversion script
python __site/convert.py

# Build Zola site
zola --root __site/build build --output-dir public
