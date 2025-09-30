# Examples and Use Cases

Real-world examples of using Airflow Breeze Manager for common development scenarios.

## Example 1: Working on Two PRs Simultaneously

**Scenario**: You're working on a feature PR but need to quickly fix a bug in a different area.

```bash
# Initial setup (one-time)
abm init --airflow-repo ~/code/airflow

# Create project for your main feature
abm add async-support --create-branch -d "Add async support to operators"
abm shell async-support

# Inside breeze, start development
breeze@container$ pytest tests/operators/
breeze@container$ exit

# Start services in background
abm docker up async-support
# Webserver now at http://localhost:28080

# Bug report comes in - need to fix urgently!
# Create new project without stopping the first one
abm add bugfix-xcom-delete -b bug/fix-xcom-delete --create-branch
abm shell bugfix-xcom-delete

# Inside breeze, work on bug
breeze@container$ pytest tests/models/test_xcom.py
breeze@container$ exit

# Start second environment (gets unique ports automatically)
abm docker up bugfix-xcom-delete
# Second webserver now at http://localhost:28081

# Both environments running simultaneously!
abm list
# ┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━┓
# ┃ Name               ┃ Branch            ┃ Backend ┃ Webserver  ┃
# ┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━┩
# │ async-support      │ async-support     │ sqlite  │ :28080     │
# │ bugfix-xcom-delete │ bug/fix-xcom-delete│ sqlite  │ :28081     │
# └────────────────────┴───────────────────┴─────────┴────────────┘

# Work on both, switching as needed
cd ~/code/airflow-worktree/async-support
abm status  # Auto-detects from directory
# Edit code...

cd ~/code/airflow-worktree/bugfix-xcom-delete
abm status  # Shows bugfix project
# Edit code...

# Clean up when bug fix is merged
abm remove bugfix-xcom-delete
```

## Example 2: Testing Multiple Database Backends

**Scenario**: You want to ensure your feature works with all supported databases.

```bash
# Create three projects with different backends
abm add test-sqlite --backend sqlite --create-branch
abm add test-postgres --backend postgres --create-branch
abm add test-mysql --backend mysql --create-branch

# Start all three environments
abm docker up test-sqlite    # Ports: 28080, 25555, etc.
abm docker up test-postgres  # Ports: 28081, 25556, etc.
abm docker up test-mysql     # Ports: 28082, 25557, etc.

# Run tests in parallel (in separate terminals)
# Terminal 1:
abm run test-sqlite pytest tests/dags/

# Terminal 2:
abm run test-postgres pytest tests/dags/

# Terminal 3:
abm run test-mysql pytest tests/dags/

# Check results
abm status test-sqlite
abm status test-postgres
abm status test-mysql

# Clean up
abm docker down test-sqlite
abm docker down test-postgres
abm docker down test-mysql
abm remove test-sqlite --force
abm remove test-postgres --force
abm remove test-mysql --force
```

## Example 3: Managing PR Reviews

**Scenario**: You want to test a colleague's PR locally.

```bash
# Create project for the PR
abm add review-feature-123 -b feature/awesome --create-branch

# Link to GitHub PR
abm pr link 12345 review-feature-123

# Enter development environment
abm shell review-feature-123

# Inside breeze, test the changes
breeze@container$ pytest tests/
breeze@container$ mypy providers/
breeze@container$ exit

# Start services to test UI changes
abm docker up review-feature-123
# Visit http://localhost:28080

# Add notes to PROJECT.md
cd ~/code/airflow-worktree/review-feature-123
cat >> PROJECT.md << EOF

## Review Notes
- Tested with SQLite backend
- All unit tests pass
- UI changes look good
- Suggested improvements:
  - Add docstrings to new functions
  - Consider edge case with null values
EOF

# Open PR to add comments
abm pr open review-feature-123

# Clean up after review
abm remove review-feature-123
```

## Example 4: Long-Running Feature Development

**Scenario**: You're working on a complex feature over weeks, need to context-switch frequently.

```bash
# Start feature development
abm add refactor-scheduler --create-branch
abm shell refactor-scheduler

# Work for a few hours...
breeze@container$ # make changes
breeze@container$ pytest tests/jobs/
breeze@container$ exit

# Emergency bug - need to switch context
# Don't remove, just freeze to save disk space
abm freeze refactor-scheduler
# Removes node_modules (~3GB saved)

# Create bug fix project
abm add hotfix-critical --create-branch
abm shell hotfix-critical
# ... fix bug ...
abm remove hotfix-critical

# Back to feature work after a few days
abm thaw refactor-scheduler
# Restores dependencies

abm shell refactor-scheduler
# Continue exactly where you left off!

# Track progress in PROJECT.md
cd ~/code/airflow-worktree/refactor-scheduler
vim PROJECT.md
# Add daily notes, TODOs, design decisions
```

## Example 5: Comparing Different Implementations

**Scenario**: You want to try two different approaches to solving a problem.

```bash
# Create two projects for different approaches
abm add approach-a --create-branch -d "Implement using async/await"
abm add approach-b --create-branch -d "Implement using threading"

# Work on approach A
abm shell approach-a
breeze@container$ # implement solution A
breeze@container$ pytest tests/
breeze@container$ exit

# Work on approach B
abm shell approach-b
breeze@container$ # implement solution B
breeze@container$ pytest tests/
breeze@container$ exit

# Benchmark both
abm run approach-a pytest tests/ --benchmark-only
abm run approach-b pytest tests/ --benchmark-only

# Start both to compare visually
abm docker up approach-a  # localhost:28080
abm docker up approach-b  # localhost:28081

# Compare side-by-side in browser
# Document findings in PROJECT.md

# Keep the winner, remove the other
abm remove approach-b
# Continue developing approach-a
```

## Example 6: Integration with Claude Code

**Scenario**: You want Claude Code to have context about your branch-specific work.

```bash
# Create project with detailed documentation
abm add implement-assets --create-branch

# Populate PROJECT.md with context
cd ~/code/airflow-worktree/implement-assets
cat > PROJECT.md << EOF
# Implement Asset-based Scheduling

## Goal
Replace dataset-based scheduling with new Asset API

## Key Changes
- New Asset model in models/asset.py
- AssetListener operator
- UI for asset graph visualization

## Design Decisions
- Using SQLAlchemy for asset relationships
- Asset version tracking via hash
- Backward compatibility maintained via migration

## Current Status
- [x] Asset model created
- [x] Basic CRUD operations
- [ ] UI components
- [ ] Integration tests
- [ ] Documentation

## References
- AIP-60: https://github.com/apache/airflow/blob/main/airflow/aip/aip-60.md
- Design doc: /docs/design/assets.md
EOF

# Start Claude Code session
# PROJECT.md is automatically available via symlink
abm shell implement-assets

# Claude can now see your PROJECT.md context!
```

## Example 7: Team Collaboration

**Scenario**: Working with a teammate on the same codebase.

```bash
# You both work on different features
# Person A:
abm add feature-auth --create-branch
abm pr link 12000 feature-auth
abm docker up feature-auth

# Person B (same machine or different):
abm add feature-ui --create-branch
abm pr link 12001 feature-ui
abm docker up feature-ui

# No conflicts! Each has unique ports:
# Person A: localhost:28080
# Person B: localhost:28081

# Share notes via PROJECT.md
cd ~/code/airflow-worktree/feature-auth
echo "## Integration Notes
- Auth hooks available in airflow.auth
- Use auth_backend='airflow.auth.custom'
" >> PROJECT.md

# Person B can read Person A's notes
cat ~/.airflow-breeze-manager/projects/feature-auth/PROJECT.md
```

## Example 8: Quick Testing of Main

**Scenario**: You want to quickly test something on main without affecting your feature branches.

```bash
# Create temporary project for main branch
abm add test-main -b main

# Quick test
abm run test-main pytest tests/operators/test_bash.py

# Or interactive debugging
abm shell test-main
breeze@container$ python -c "import airflow; print(airflow.__version__)"
breeze@container$ exit

# Clean up (no need to keep)
abm remove test-main --force
```

## Example 9: Disk Space Management

**Scenario**: You have limited disk space but multiple active projects.

```bash
# Create multiple projects
abm add feature-1 --create-branch
abm add feature-2 --create-branch
abm add feature-3 --create-branch
abm add feature-4 --create-branch

# All projects take up space (~3GB each = 12GB total)
# Freeze inactive projects
abm freeze feature-2
abm freeze feature-3
abm freeze feature-4

# Now only feature-1 uses full space
# Others are ~200MB each

# List shows frozen status
abm list
# ┏━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━┓
# ┃ Name     ┃ Branch   ┃ Backend ┃ Webserver  ┃ Status  ┃
# ┡━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━┩
# │ feature-1│ feature-1│ sqlite  │ :28080     │ ✓       │
# │ feature-2│ feature-2│ sqlite  │ :28081     │ 🧊      │
# │ feature-3│ feature-3│ sqlite  │ :28082     │ 🧊      │
# │ feature-4│ feature-4│ sqlite  │ :28083     │ 🧊      │
# └──────────┴──────────┴─────────┴────────────┴─────────┘

# When ready to work on feature-2 again
abm thaw feature-2
# Dependencies restored automatically
```

## Example 10: CI/CD Reproduction

**Scenario**: CI failed on your PR, need to reproduce locally.

```bash
# Create project matching CI environment
abm add ci-debug -b my-feature --backend postgres --python-version 3.11

# Enter shell with same backend as CI
abm shell ci-debug

# Inside breeze, run exact CI command
breeze@container$ pytest tests/providers/ -v --tb=short

# Debug failure
breeze@container$ pytest tests/providers/test_failing.py -vv --pdb

# Try fix
breeze@container$ # edit code
breeze@container$ pytest tests/providers/test_failing.py
breeze@container$ exit

# Push fix and clean up
abm remove ci-debug
```

## Tips from Examples

1. **Use descriptive names**: `bugfix-xcom-delete` is better than `branch1`
2. **Link PRs immediately**: `abm pr link` helps track progress
3. **Document in PROJECT.md**: Future you will thank present you
4. **Freeze inactive projects**: Save disk space without losing context
5. **Use auto-detection**: Commands work without project name when in directory
6. **Leverage parallel testing**: Multiple backends, multiple versions
7. **Keep temporary branches**: Use `--keep-docs` to preserve notes
8. **Combine with aliases**: Create shell aliases for common workflows

## Shell Aliases

Add to your `~/.bashrc` or `~/.zshrc`:

```bash
# Quick aliases
alias abml='abm list'
alias abms='abm status'
alias abmsh='abm shell'

# Quick project creation
abmnew() {
    abm add "$1" --create-branch && abm shell "$1"
}

# Quick cleanup
abmrm() {
    abm docker down "$1" && abm remove "$1" --force
}
```

Usage:
```bash
abmnew my-feature  # Create and enter in one command
abml               # Quick list
abmrm old-feature  # Quick remove
```
