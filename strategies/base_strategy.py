class BaseStrategy:
    def __init__(self, node):
        self.node = node

    def get_name(self):
        """Returns the name of the strategy."""
        return "Base Strategy"

    def calculate_score(self, task_data):
        """
        Calculates and returns a qualify score (0-100) for a given task.
        Must be implemented by subclasses.
        """
        raise NotImplementedError("Subclasses must implement calculate_score")

    def on_task_won(self, task_data):
        """
        Called when the node wins an election for a task.
        Allows the strategy to track active tasks, allocate resources, etc.
        """
        pass

    def on_task_completed(self, task_id):
        """
        Called when a task finishes execution.
        """
        pass
