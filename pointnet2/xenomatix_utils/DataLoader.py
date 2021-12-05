import os
import numpy as np

from tqdm import tqdm
from torch.utils.data import Dataset


class S3DISDataset(Dataset):
    def __init__(self, split='train', data_root='trainval_fullarea', num_point=4096, test_scene=1, block_size=1.0, sample_rate=1.0, transform=None):
        super().__init__()
        self.num_point = num_point
        self.block_size = block_size
        self.transform = transform
        frames = sorted(os.listdir(data_root))
        frames = [frame for frame in frames if 'Scene_' in frame]
        if split == 'train':
            frames_split = [frame for frame in frames if not 'Scene_{}'.format(test_scene) in frame]
        else:
            frames_split = [frame for frame in frames if 'Scene_{}'.format(test_scene) in frame]

        self.frame_points, self.frame_labels = [], []
        self.frame_coord_min, self.frame_coord_max = [], []
        num_point_all = []
        labelweights = np.zeros(2)

        for frame_name in tqdm(frames_split, total=len(frames_split)):
            frame_path = os.path.join(data_root, frame_name)
            print(frame_path)
            frame_data = np.load(frame_path)  # xyzil, N*5
            points, labels = frame_data[:, 0:4], frame_data[:, 4]  # xyzi, N*4; l, N
            tmp, _ = np.histogram(labels, range(2))
            labelweights += tmp
            coord_min, coord_max = np.amin(points, axis=0)[:3], np.amax(points, axis=0)[:3]
            self.frame_points.append(points), self.frame_labels.append(labels)
            self.frame_coord_min.append(coord_min), self.frame_coord_max.append(coord_max)
            num_point_all.append(labels.size)
        labelweights = labelweights.astype(np.float32)
        labelweights = labelweights / np.sum(labelweights)
        self.labelweights = np.power(np.amax(labelweights) / labelweights, 1 / 3.0)
        print(self.labelweights)
        sample_prob = num_point_all / np.sum(num_point_all)
        num_iter = int(np.sum(num_point_all) * sample_rate / num_point)
        frame_idxs = []
        for index in range(len(frames_split)):
            frame_idxs.extend([index] * int(round(sample_prob[index] * num_iter)))
        self.frame_idxs = np.array(frame_idxs)
        print("Totally {} samples in {} set.".format(len(self.frame_idxs), split))

    def __getitem__(self, idx):
        frame_idx = self.frame_idxs[idx]
        points = self.frame_points[frame_idx]   # N * 6
        labels = self.frame_labels[frame_idx]   # N
        N_points = points.shape[0]

        while (True):
            center = points[np.random.choice(N_points)][:3]
            block_min = center - [self.block_size / 2.0, self.block_size / 2.0, 0]
            block_max = center + [self.block_size / 2.0, self.block_size / 2.0, 0]
            point_idxs = np.where((points[:, 0] >= block_min[0]) & (points[:, 0] <= block_max[0]) & (points[:, 1] >= block_min[1]) & (points[:, 1] <= block_max[1]))[0]
            # print("Point size: ", point_idxs.size)
            if point_idxs.size > 4096:
                break

        if point_idxs.size >= self.num_point:
            selected_point_idxs = np.random.choice(point_idxs, self.num_point, replace=False)
        else:
            selected_point_idxs = np.random.choice(point_idxs, self.num_point, replace=True)

        # normalize
        selected_points = points[selected_point_idxs, :]  # num_point * 4
        current_points = np.zeros((self.num_point, 7))  # num_point * 7
        current_points[:, 4] = selected_points[:, 0] / self.frame_coord_max[frame_idx][0]
        current_points[:, 5] = selected_points[:, 1] / self.frame_coord_max[frame_idx][1]
        current_points[:, 6] = selected_points[:, 2] / self.frame_coord_max[frame_idx][2]
        selected_points[:, 0] = selected_points[:, 0] - center[0]
        selected_points[:, 1] = selected_points[:, 1] - center[1]
        # selected_points[:, 3:6] /= 255.0
        current_points[:, 0:4] = selected_points
        current_labels = labels[selected_point_idxs]
        if self.transform is not None:
            current_points, current_labels = self.transform(current_points, current_labels)
        return current_points, current_labels

    def __len__(self):
        return len(self.frame_idxs)

class ScannetDatasetWholeScene():
    # prepare to give prediction on each points
    def __init__(self, root, block_points=4096, split='test', test_scene=3, stride=0.5, block_size=1.0, padding=0.001):
        self.block_points = block_points
        self.block_size = block_size
        self.padding = padding
        self.root = root
        self.split = split
        self.stride = stride
        self.scene_points_num = []
        assert split in ['train', 'test']
        if self.split == 'train':
            self.file_list = [d for d in os.listdir(root) if d.find('Scene_%d' % test_scene) == -1]
        else:
            self.file_list = [d for d in os.listdir(root) if d.find('Scene_%d' % test_scene) != -1]
        self.scene_points_list = []
        self.semantic_labels_list = []
        self.frame_coord_min, self.frame_coord_max = [], []
        for file in self.file_list:
            print(root, file)
            data = np.load(root + '/' + file)
            points = data[:, :3]
            self.scene_points_list.append(data[:, :4])
            self.semantic_labels_list.append(data[:, 4])
            coord_min, coord_max = np.amin(points, axis=0)[:3], np.amax(points, axis=0)[:3]
            self.frame_coord_min.append(coord_min), self.frame_coord_max.append(coord_max)
        assert len(self.scene_points_list) == len(self.semantic_labels_list)

        labelweights = np.zeros(13)
        for seg in self.semantic_labels_list:
            tmp, _ = np.histogram(seg, range(14))
            self.scene_points_num.append(seg.shape[0])
            labelweights += tmp
        labelweights = labelweights.astype(np.float32)
        labelweights = labelweights / np.sum(labelweights)
        self.labelweights = np.power(np.amax(labelweights) / labelweights, 1 / 3.0)

    def __getitem__(self, index):
        point_set_ini = self.scene_points_list[index]
        points = point_set_ini[:,:6]
        labels = self.semantic_labels_list[index]
        coord_min, coord_max = np.amin(points, axis=0)[:3], np.amax(points, axis=0)[:3]
        grid_x = int(np.ceil(float(coord_max[0] - coord_min[0] - self.block_size) / self.stride) + 1)
        grid_y = int(np.ceil(float(coord_max[1] - coord_min[1] - self.block_size) / self.stride) + 1)
        data_frame, label_frame, sample_weight, index_frame = np.array([]), np.array([]), np.array([]),  np.array([])
        for index_y in range(0, grid_y):
            for index_x in range(0, grid_x):
                s_x = coord_min[0] + index_x * self.stride
                e_x = min(s_x + self.block_size, coord_max[0])
                s_x = e_x - self.block_size
                s_y = coord_min[1] + index_y * self.stride
                e_y = min(s_y + self.block_size, coord_max[1])
                s_y = e_y - self.block_size
                point_idxs = np.where(
                    (points[:, 0] >= s_x - self.padding) & (points[:, 0] <= e_x + self.padding) & (points[:, 1] >= s_y - self.padding) & (
                                points[:, 1] <= e_y + self.padding))[0]
                if point_idxs.size == 0:
                    continue
                num_batch = int(np.ceil(point_idxs.size / self.block_points))
                point_size = int(num_batch * self.block_points)
                replace = False if (point_size - point_idxs.size <= point_idxs.size) else True
                point_idxs_repeat = np.random.choice(point_idxs, point_size - point_idxs.size, replace=replace)
                point_idxs = np.concatenate((point_idxs, point_idxs_repeat))
                np.random.shuffle(point_idxs)
                data_batch = points[point_idxs, :]
                normlized_xyz = np.zeros((point_size, 3))
                normlized_xyz[:, 0] = data_batch[:, 0] / coord_max[0]
                normlized_xyz[:, 1] = data_batch[:, 1] / coord_max[1]
                normlized_xyz[:, 2] = data_batch[:, 2] / coord_max[2]
                data_batch[:, 0] = data_batch[:, 0] - (s_x + self.block_size / 2.0)
                data_batch[:, 1] = data_batch[:, 1] - (s_y + self.block_size / 2.0)
                # data_batch[:, 3:6] /= 255.0
                data_batch = np.concatenate((data_batch, normlized_xyz), axis=1)
                label_batch = labels[point_idxs].astype(int)
                batch_weight = self.labelweights[label_batch]

                data_frame = np.vstack([data_frame, data_batch]) if data_frame.size else data_batch
                label_frame = np.hstack([label_frame, label_batch]) if label_frame.size else label_batch
                sample_weight = np.hstack([sample_weight, batch_weight]) if label_frame.size else batch_weight
                index_frame = np.hstack([index_frame, point_idxs]) if index_frame.size else point_idxs
        data_frame = data_frame.reshape((-1, self.block_points, data_frame.shape[1]))
        label_frame = label_frame.reshape((-1, self.block_points))
        sample_weight = sample_weight.reshape((-1, self.block_points))
        index_frame = index_frame.reshape((-1, self.block_points))
        return data_frame, label_frame, sample_weight, index_frame

    def __len__(self):
        return len(self.scene_points_list)

if __name__ == '__main__':
    # data_root = '/home/test/Pointnet_Pointnet2_pytorch/pointnet2/sample/output'
    # data_root = '/home/zhukeyue/Documents/XenomatiX/sample/output'
    data_root = os.getcwd()
    data_root = os.path.abspath(os.path.join(data_root, os.pardir))
    data_root = os.path.join(data_root, 'sample', 'output')
    num_point, test_scene, block_size, sample_rate = 4096, 3, 10000.0, 1

    point_data = S3DISDataset(split='test', data_root=data_root, num_point=num_point, test_scene=test_scene, block_size=block_size, sample_rate=sample_rate, transform=None)
    print('point data size:', point_data.__len__())
    print('point data 0 shape:', point_data.__getitem__(0)[0].shape)
    print('point label 0 shape:', point_data.__getitem__(0)[1].shape)
    import torch, time, random
    manual_seed = 123
    random.seed(manual_seed)
    np.random.seed(manual_seed)
    torch.manual_seed(manual_seed)
    torch.cuda.manual_seed_all(manual_seed)
    def worker_init_fn(worker_id):
        random.seed(manual_seed + worker_id)
    train_loader = torch.utils.data.DataLoader(point_data, batch_size=16, shuffle=True, num_workers=0, pin_memory=True, worker_init_fn=worker_init_fn)
    for idx in range(4):
        end = time.time()
        for i, (input, target) in enumerate(train_loader):
            print('time: {}/{}--{}'.format(i+1, len(train_loader), time.time() - end))
            end = time.time()