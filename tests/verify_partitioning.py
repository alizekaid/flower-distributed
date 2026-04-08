import unittest
from unittest.mock import MagicMock, patch
import torch
from torch.utils.data import Subset
import sys
import os

# Add src to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from flower_distributed.task import load_data

class TestDataPartitioning(unittest.TestCase):
    
    @patch('flower_distributed.task.CIFAR10')
    def test_consistent_partitioning(self, mock_cifar):
        # Create a mock dataset of 100 items
        mock_dataset = MagicMock()
        mock_dataset.__len__.return_value = 100
        # Mocking the indexing behavior
        mock_dataset.__getitem__.side_effect = lambda i: (torch.randn(3, 32, 32), i % 10)
        
        mock_cifar.return_value = mock_dataset
        
        # We need to clear the cache in task.py if it exists
        import flower_distributed.task as task
        task._global_dataset = None
        
        # Run 1: Get partition 0
        train1, test1 = load_data(partition_id=0, num_partitions=10)
        indices1 = list(train1.dataset.indices) + list(test1.dataset.indices)
        
        # Clear cache for "second run" simulation
        task._global_dataset = None
        
        # Run 2: Get partition 0 again
        train2, test2 = load_data(partition_id=0, num_partitions=10)
        indices2 = list(train2.dataset.indices) + list(test2.dataset.indices)
        
        # Check if indices are identical
        self.assertEqual(indices1, indices2, "Partition 0 indices should be identical across runs")
        
        # Run 3: Get partition 1
        task._global_dataset = None
        train3, test3 = load_data(partition_id=1, num_partitions=10)
        indices3 = list(train3.dataset.indices) + list(test3.dataset.indices)
        
        # Check if indices 1 and 3 are different
        self.assertNotEqual(indices1, indices3, "Different partitions should have different indices")
        
        print("\n✅ Verification PASSED: Partitioning is consistent across simulated runs.")

if __name__ == '__main__':
    unittest.main()
