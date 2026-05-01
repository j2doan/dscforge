import numpy as np
import pandas as pd
from matplotlib import pyplot as plt

from src import config

def load_csv(path):
    """Load a CSV file into a pandas DataFrame."""
    return pd.read_csv(path)