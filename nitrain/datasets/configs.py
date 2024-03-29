import glob
import os
from parse import parse
from fnmatch import fnmatch

import datalad.api as dl
import pandas as pd
import numpy as np
import ants

class ComposeConfig:
    def __init__(self, configs):
        self.configs = configs
        values = [config.values for config in self.configs]
        self.values = list(zip(*values))
        
        # TODO: align ids for composed configs
        if self.configs[0].ids is not None:
            self.ids = self.configs[0].ids
        else:
            self.ids = None

    def __getitem__(self, idx):
        return [config[idx] for config in self.configs]

# list of ants images
class ImageConfig:
    def __init__(self, images):
        self.values = images

    def __getitem__(self, idx):
        return self.values[idx]


# numpy array that must be converted to images
class ArrayConfig:
    def __init__(self, array):
        """
        x = np.random.normal(40,10,(10,50,50,50))
        x_config = ArrayConfig(x)
        """
        self.array = array
        
        if array.ndim > 2:
            # arrays must be converted to images
            array_list = np.split(array, array.shape[0])
            ns = array.shape[1:]
            self.values = [ants.from_numpy(tmp.reshape(*ns)) for tmp in array_list]
        else:
            self.values = array
        
    def __getitem__(self, idx):
        return self.values[idx]

# one image from file
class PatternConfig:
    def __init__(self, base_dir, pattern, exclude=None, datalad=False):
        if not base_dir.endswith('/'):
            base_dir += '/'
            
        glob_pattern = pattern.replace('{id}','*')
        glob_pattern = os.path.join(base_dir, glob_pattern)
        x = sorted(glob.glob(glob_pattern, recursive=True))
        x = [os.path.relpath(xx, base_dir) for xx in x]
        
        if exclude:
            x = [file for file in x if not fnmatch(file, exclude)]

        if '{id}' in pattern:
            ids = [parse(pattern.replace('*','{other}'), file).named['id'] for file in x]
        else:
            ids = None
            
        x = [os.path.join(base_dir, file) for file in x]
        
        if len(x) == 0:
            raise Exception(f'No filepaths found that match {glob_pattern}')

        self.base_dir = base_dir
        self.pattern = glob_pattern
        self.exclude = exclude
        self.datalad = datalad
        self.values = x
        self.ids = ids
        
    def __getitem__(self, idx):
        filename = self.values[idx]
        
        if self.datalad:
            ds = dl.Dataset(path = self.base_dir)
            res = ds.get(filename)
            
        return ants.image_read(self.values[idx])
    
class ColumnConfig:
    def __init__(self, base_dir, file, column, id=None):
        filepath = os.path.join(base_dir, file)
        
        if not os.path.exists(filepath):
            raise Exception(f'No file found at {filepath}')
        
        if filepath.endswith('.tsv'):
            participants = pd.read_csv(filepath, sep='\t')
        elif filepath.endswith('.csv'):
            participants = pd.read_csv(filepath)
            
        values = participants[column].to_numpy()
        
        if id is not None:
            ids = list(participants[id].to_numpy())
        else:
            ids = None
        
        self.base_dir = base_dir
        self.values = values
        self.ids = ids
        self.file = filepath
        self.column = column

    def __getitem__(self, idx):
        return self.values[idx]
    
class GoogleCloudConfig:
    def __init__(self, bucket, base_dir, pattern, exclude=None, fuse=False, lazy=False):
        pass

def _infer_config(x, base_dir=None):
    """
    Infer config from user-supplied values
    
    Examples
    --------
    >>> base_dir = os.path.expanduser('~/Desktop/openneuro/ds004711')
    >>> array = np.random.normal(40,10,(10,50,50,50))
    >>> x = _infer_config(array)
    >>> x = _infer_config([ants.image_read(ants.get_data('r16')) for _ in range(10)])
    >>> x = _infer_config([{'pattern': '{id}/anat/*.nii.gz'}, {'pattern': '{id}/anat/*.nii.gz'}], base_dir) 
    >>> x = _infer_config({'pattern': '{id}/anat/*.nii.gz'}, base_dir) 
    >>> x = _infer_config({'pattern': '*/anat/*.nii.gz'}, base_dir)
    >>> x = _infer_config({'pattern': '**/*T1w*'}, base_dir) 
    >>> x = _infer_config({'file': 'participants.tsv', 'column': 'age', 'id': 'participant_id'}, base_dir) 
    >>> x = _infer_config({'file': 'participants.tsv', 'column': 't1', 'image': True}, base_dir) 
    """
    if isinstance(x, list):
        # list of ants images
        if isinstance(x[0], ants.ANTsImage):
            return ImageConfig(x)
        # list of multiple ()potentially mixed) configs
        elif isinstance(x[0], dict):
            configs = [_infer_config(config, base_dir=base_dir) for config in x]
            return ComposeConfig(configs)
        # list that is meant to be an array
        else:
            return ArrayConfig(np.array(x))
        
    elif isinstance(x, dict):
        if 'pattern' in x.keys():
            return PatternConfig(base_dir=base_dir, **x)
        if 'file' in x.keys():
            return ColumnConfig(base_dir=base_dir, **x)
        
    elif isinstance(x, np.ndarray):
        return ArrayConfig(x)


def _align_configs(x, y):
    """
    Align configs based on ID or something other pattern.
    """
    if x.ids is None:
        raise Exception('`x` is missing ids. Specify `{id}` somewhere in the pattern.')
    if y.ids is None:
        if isinstance(y, PatternConfig):
            raise Exception('`y` is missing ids. Specify `{id}` somewhere in the pattern.')
        elif isinstance(y, ColumnConfig):
            raise Exception('`y` is missing ids. Specify "id": "COL_NAME" in the file dict.')
    
    x_ids = x.ids
    y_ids = y.ids
    
    # match ids
    matched_ids = sorted(list(set(x_ids) & set(y_ids)))
    if len(matched_ids) == 0:
        raise Exception('No matches found between `x` ids and `y` ids. Double check your config.')
    
    # take only matched ids in x
    keep_idx = [i for i in range(len(x.ids)) if x.ids[i] in matched_ids]
    x.ids = matched_ids
    x.values = [x.values[i] for i in keep_idx]

    # take only matched ids in y
    keep_idx = [i for i in range(len(y.ids)) if y.ids[i] in matched_ids]
    y.ids = matched_ids
    if isinstance(y.values, np.ndarray):
        y.values = np.array([y.values[i] for i in keep_idx])
    else:
        y.values = [y.values[i] for i in keep_idx]

    return x, y

