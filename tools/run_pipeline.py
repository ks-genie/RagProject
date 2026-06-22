# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, ".")

from src.log_setup import setup_logging
setup_logging()

from src.pipeline import Pipeline

p = Pipeline()
stats = p.run_once()
print(stats.summary())
