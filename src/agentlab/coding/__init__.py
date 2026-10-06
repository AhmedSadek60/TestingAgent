"""What AgentLab needs to test a coding agent: bundled projects to work on and a way to compare the result."""

from agentlab.coding.diffing import TreeDiff, diff_trees, read_tree
from agentlab.coding.projects import PROJECTS, WorkspaceProject, get_project, materialize, safe_relative

__all__ = [
    "PROJECTS",
    "TreeDiff",
    "WorkspaceProject",
    "diff_trees",
    "get_project",
    "materialize",
    "read_tree",
    "safe_relative",
]
