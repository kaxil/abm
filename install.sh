#!/bin/bash
# Quick installation script for Airflow Breeze Manager

set -e

echo "🚀 Installing Airflow Breeze Manager (ABM)"
echo ""

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "❌ uv is not installed."
    echo ""
    echo "Please install uv first:"
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo ""
    echo "Or visit: https://github.com/astral-sh/uv"
    exit 1
fi

echo "✓ uv found: $(uv --version)"
echo ""

# Install ABM
echo "Installing airflow-breeze-manager..."
uv tool install airflow-breeze-manager

echo ""
echo "✅ Installation complete!"
echo ""
echo "Quick start:"
echo "  abm init                    # Initialize ABM"
echo "  abm add my-feature         # Create your first project"
echo "  abm --help                 # See all commands"
echo ""
echo "Documentation: https://github.com/astronomer/airflow-claude"
