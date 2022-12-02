from typing import Any, Dict


class Step:
    """StepInterface"""

    # name of step to be overriden
    @property
    def name(self):
        return repr(self)

    def __repr__(self):
        return self.__class__.__name__

    def __str__(self):
        return self.name

    def run(self, payload: Dict[str, Any]) -> int:
        """actual step implementation. 0 on success"""
        raise NotImplementedError()

    def cleanup(self, payload: Dict[str, Any]):
        """clean resources reserved in run()"""
        ...


class VirtualInitStep(Step):
    ...
