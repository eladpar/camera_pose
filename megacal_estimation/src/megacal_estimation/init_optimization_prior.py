import sys, time, optparse
import itertools
import collections

import roslib
roslib.load_manifest('megacal_estimation')
import PyKDL
from calibration_msgs.msg import *
from tf_conversions import posemath

import rosbag


def read_observations(meas):
    # Stores the checkerboards observed by two cameras
    # camera_id -> camera_id -> [ (cb pose, cb pose, cb id) ]
    mutual_observations = collections.defaultdict(lambda: collections.defaultdict(list))

    checkerboard_id = 0
    for msg in meas:
        for M_cam1, M_cam2 in itertools.combinations(msg.M_cam, 2):
            cam1 = M_cam1.camera_id
            cam2 = M_cam2.camera_id
            p1 = posemath.fromMsg(M_cam1.features.object_pose.pose)
            p2 = posemath.fromMsg(M_cam2.features.object_pose.pose)

            mutual_observations[cam1][cam2].append( (p1, p2, checkerboard_id) )
            mutual_observations[cam2][cam1].append( (p2, p1, checkerboard_id) )

        checkerboard_id += 1
    return mutual_observations

# Populates cameras_seen and checkerboards_seen
# root_cam: camera id to start from
# observations: camera_id -> camera_id -> [ (cb pose, cb pose, checkerbord id) ]
# cameras_seen: camera_id -> pose
# checkerboards_seen: checkerboard_id -> pose
def bfs(root_cam, observations, cameras_seen, checkerboards_seen):
    q = [root_cam]
    cameras_seen[root_cam] = PyKDL.Frame()
    while q:
        cam1, q = q[0], q[1:]  # pop
        cam1_pose = cameras_seen[cam1]

        for cam2, checkerboards in observations[cam1].iteritems():
            assert checkerboards
            if cam2 not in cameras_seen:
                q.append(cam2)

                # Cam2Pose = Cam1Pose * Cam1->CB * CB->Cam2
                cameras_seen[cam2] = cam1_pose * checkerboards[0][0] * checkerboards[0][1].Inverse()

            for cb in checkerboards:
                if cb[2] not in checkerboards_seen:
                    checkerboards_seen[cb[2]] = cam1_pose * cb[0]

def find_initial_poses(meas, root_cam = None):
    mutual_observations = read_observations(meas)

    if not root_cam:
        root_cam = mutual_observations.keys()[0]
    camera_poses = {}
    checkerboard_poses = {}
    bfs(root_cam, mutual_observations, camera_poses, checkerboard_poses)
    return camera_poses, checkerboard_poses
