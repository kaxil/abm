from __future__ import annotations

from airflow_breeze_manager.models import ProjectMetadata, ProjectPorts


def test_project_ports_default() -> None:
    """Test default ports creation."""
    ports = ProjectPorts.default()
    assert ports.webserver == 28180
    assert ports.flower == 25655
    assert ports.postgres == 25533
    assert ports.mysql == 23406
    assert ports.redis == 26479
    assert ports.ssh == 12422


def test_project_ports_to_env_dict() -> None:
    """Test port conversion to environment variables."""
    ports = ProjectPorts(
        webserver=28181,
        flower=25656,
        postgres=25534,
        mysql=23407,
        redis=26480,
        ssh=12423,
    )
    env_dict = ports.to_env_dict()
    assert env_dict["WEB_HOST_PORT"] == "28181"
    assert env_dict["FLOWER_HOST_PORT"] == "25656"
    assert env_dict["POSTGRES_HOST_PORT"] == "25534"
    assert env_dict["MYSQL_HOST_PORT"] == "23407"
    assert env_dict["REDIS_HOST_PORT"] == "26480"
    assert env_dict["SSH_PORT"] == "12423"


def test_project_ports_to_env_dict_with_project_name() -> None:
    """Test port conversion with project name for Airflow UI."""
    ports = ProjectPorts.default()
    env_dict = ports.to_env_dict(project_name="my-feature")
    assert env_dict["AIRFLOW__API__INSTANCE_NAME"] == "ABM: my-feature"


def test_project_metadata_serialization() -> None:
    """Test project metadata serialization."""
    ports = ProjectPorts.default()
    project = ProjectMetadata(
        name="test-project",
        branch="main",
        worktree_path="/tmp/test",
        ports=ports,
        description="Test project",
    )

    # Serialize
    data = project.to_dict()
    assert data["name"] == "test-project"
    assert data["branch"] == "main"

    # Deserialize
    restored = ProjectMetadata.from_dict(data)
    assert restored.name == project.name
    assert restored.branch == project.branch
    assert restored.ports.webserver == project.ports.webserver


def test_project_metadata_migration_missing_ssh_port() -> None:
    """Test migration for old projects missing SSH port."""
    data = {
        "name": "old-project",
        "branch": "main",
        "worktree_path": "/tmp/test",
        "description": "",
        "pr_number": None,
        "backend": "sqlite",
        "python_version": "3.11",
        "created_at": "",
        "frozen": False,
        "ports": {
            "webserver": 28180,
            "flower": 25655,
            "postgres": 25533,
            "mysql": 23406,
            "redis": 26479,
            # SSH missing
        },
    }

    restored = ProjectMetadata.from_dict(data)
    assert restored.ports.ssh == 12422  # Should be calculated


def test_project_metadata_migration_old_defaults() -> None:
    """Test migration from old port defaults to new defaults."""
    data = {
        "name": "old-project",
        "branch": "main",
        "worktree_path": "/tmp/test",
        "description": "",
        "pr_number": None,
        "backend": "sqlite",
        "python_version": "3.11",
        "created_at": "",
        "frozen": False,
        "ports": {
            "webserver": 28080,  # Old default
            "flower": 25555,  # Old default
            "postgres": 25433,  # Old default
            "mysql": 23306,  # Old default
            "redis": 26379,  # Old default
            "ssh": 12322,  # Old default
        },
    }

    restored = ProjectMetadata.from_dict(data)
    # Should be migrated to new defaults
    assert restored.ports.webserver == 28180
    assert restored.ports.flower == 25655
    assert restored.ports.ssh == 12422


def test_project_metadata_save_load(tmp_path) -> None:
    """Test saving and loading project metadata."""

    project_dir = tmp_path / "test-project"
    project_dir.mkdir()

    ports = ProjectPorts.default()
    original = ProjectMetadata(
        name="test-project",
        branch="feature/test",
        worktree_path="/tmp/test",
        ports=ports,
        description="Test description",
        pr_number=12345,
    )

    # Save
    original.save(project_dir)
    assert (project_dir / ".abm").exists()

    # Load
    loaded = ProjectMetadata.load(project_dir)
    assert loaded.name == original.name
    assert loaded.branch == original.branch
    assert loaded.pr_number == original.pr_number
    assert loaded.ports.webserver == original.ports.webserver


def test_global_config_serialization() -> None:
    """Test global config serialization."""
    from airflow_breeze_manager.models import GlobalConfig

    config = GlobalConfig(schema_version=1, airflow_repo="/home/user/airflow", worktree_base="/home/user/worktrees")

    # Serialize
    data = config.to_dict()
    assert data["schema_version"] == 1
    assert data["airflow_repo"] == "/home/user/airflow"

    # Deserialize
    restored = GlobalConfig.from_dict(data)
    assert restored.schema_version == config.schema_version
    assert restored.airflow_repo == config.airflow_repo


def test_global_config_save_load(tmp_path) -> None:
    """Test saving and loading global config."""

    from airflow_breeze_manager.models import GlobalConfig

    config_file = tmp_path / ".abm.json"
    original = GlobalConfig(schema_version=1, airflow_repo="/home/user/airflow", worktree_base="/home/user/worktrees")

    # Save
    original.save(config_file)
    assert config_file.exists()

    # Load
    loaded = GlobalConfig.load(config_file)
    assert loaded is not None
    assert loaded.schema_version == original.schema_version
    assert loaded.airflow_repo == original.airflow_repo


def test_global_config_load_nonexistent(tmp_path) -> None:
    """Test loading non-existent config file."""

    from airflow_breeze_manager.models import GlobalConfig

    config_file = tmp_path / "nonexistent.json"
    loaded = GlobalConfig.load(config_file)
    assert loaded is None


def test_global_config_load_corrupted(tmp_path) -> None:
    """Test loading corrupted config file."""

    from airflow_breeze_manager.models import GlobalConfig

    config_file = tmp_path / "corrupted.json"
    config_file.write_text("{ invalid json }")

    loaded = GlobalConfig.load(config_file)
    assert loaded is None  # Should return None for corrupted file
