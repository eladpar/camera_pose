#!/usr/bin/env python

import cv2
from cv_bridge import CvBridge, CvBridgeError
import rospy
import threading
import numpy
from calibration_msgs.msg import Interval
from calibration_msgs.msg import CalibrationPattern
from sensor_msgs.msg import Image
from sensor_msgs.msg import CameraInfo
from camera_pose_calibration.msg import RobotMeasurement
from camera_pose_calibration.msg import CameraCalibration


def beep(conf):
    try:
        with file('/dev/audio', 'wb') as audio:
            for (frequency, amplitude, duration) in conf:
                sample = 8000
                half_period = int(sample / frequency / 2)
                beep = chr(amplitude) * half_period + chr(0) * half_period
                beep *= int(duration * frequency)
                audio.write(beep)
    except:
        print("Beep beep")


class ImageRenderer:
    def __init__(self, ns):
        self.lock = threading.Lock()
        self.image_time = rospy.Time(0)
        self.info_time = rospy.Time(0)
        self.image = None
        self.interval = 0
        self.features = None
        self.bridge = CvBridge()
        self.ns = ns
        self.max_interval = rospy.get_param('filter_intervals/min_duration')

        self.info_sub = rospy.Subscriber(ns + '/camera_info', CameraInfo, self.info_cb)
        self.image_sub = rospy.Subscriber(ns + '/image_throttle', Image, self.image_cb)
        self.interval_sub = rospy.Subscriber(ns + '/settled_interval', Interval, self.interval_cb)
        self.features_sub = rospy.Subscriber(ns + '/features', CalibrationPattern, self.features_cb)

    def info_cb(self, msg):
        with self.lock:
            self.info_time = rospy.Time.now()

    def image_cb(self, msg):
        with self.lock:
            self.image_time = rospy.Time.now()
            self.image = msg

    def interval_cb(self, msg):
        with self.lock:
            self.interval = (msg.end - msg.start).to_sec()

    def features_cb(self, msg):
        with self.lock:
            self.features = msg

    def render(self, window):
        with self.lock:
            if self.image and self.image_time + rospy.Duration(8.0) > rospy.Time.now() and self.info_time + rospy.Duration(8.0) > rospy.Time.now():
                window[:, :, :] = cv2.resize(self.bridge.imgmsg_to_cv2(self.image, 'rgb8'), (window.shape[1], window.shape[0]))
                # render progress bar
                interval = min(1, (self.interval / self.max_interval))
                cv2.rectangle(window,
                              (int(0.05 * window.shape[1]), int(window.shape[0] * 0.9)),
                              (int(interval * window.shape[1] * 0.9 + 0.05 * window.shape[1]),
                               int(window.shape[0] * 0.95)),
                              (0, interval * 255, (1 - interval) * 255), thickness=-1)
                cv2.rectangle(window,
                              (int(0.05 * window.shape[1]), int(window.shape[0] * 0.9)),
                              (int(window.shape[1] * 0.9 + 0.05 * window.shape[1]), int(window.shape[0] * 0.95)),
                              (0, interval * 255, (1 - interval) * 255))
                cv2.putText(window, self.ns, (int(window.shape[1] * .05), int(window.shape[0] * 0.1)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), thickness=1)

                if self.features and self.features.header.stamp + rospy.Duration(4.0) > self.image.header.stamp:
                    w_scaling = float(window.shape[1]) / self.image.width
                    h_scaling = float(window.shape[0]) / self.image.height
                    if self.features.success:
                        corner_color = (0, 255, 0)
                        for cur_pt in self.features.image_points:
                            cv2.circle(window, (int(cur_pt.x * w_scaling), int(cur_pt.y * h_scaling)),
                                       int(w_scaling * 5), corner_color)
                    else:
                        window = add_text(window, ["Could not detect", "checkerboard"], False)
                else:
                    window = add_text(window, ["Timed out waiting", "for checkerboard"], False)

            else:
                # Generate random white noise (for fun)
                noise = numpy.random.rand(window.shape[0], window.shape[1]) * 256
                numpy.asarray(window)[:, :, 0] = noise
                numpy.asarray(window)[:, :, 1] = noise
                numpy.asarray(window)[:, :, 2] = noise
                cv2.putText(window, self.ns, (int(window.shape[1] * .05), int(window.shape[0] * .95)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), thickness=2)


def add_text(image, text, good=True):
    if good:
        color = (0, 255, 0)
    else:
        color = (0, 0, 255)
    h = image.shape[0]
    w = image.shape[1]
    for i in range(len(text)):
        ((text_w, text_h), _) = cv2.getTextSize(text[i], cv2.FONT_HERSHEY_SIMPLEX, 1.0, 1)
        cv2.putText(image, text[i], (int(w / 2 - text_w / 2),int( h / 2 - text_h / 2 + i * text_h * 2)), cv2.FONT_HERSHEY_SIMPLEX,
                    1.0, color, thickness=2)
    return image


def get_image(text, good=True, h=480, w=640):
    image = numpy.zeros((h, w, 3), numpy.uint8)
    return add_text(image, text, good)


class Aggregator:
    def __init__(self, ns_list):
        print("Creating aggregator for ", ns_list)

        self.lock = threading.Lock()

        # image
        w = 640
        h = 480
        self.image_out = numpy.zeros((h, w, 3), numpy.uint8)
        self.pub = rospy.Publisher('aggregated_image', Image, queue_size=10)
        self.bridge = CvBridge()

        self.image_captured = get_image(["Successfully captured checkerboard"])
        self.image_optimized = get_image(["Successfully ran optimization"])
        self.image_failed = get_image(["Failed to run optimization"], False)

        # create render windows
        layouts = [(1, 1), (2, 2), (2, 2), (2, 2), (3, 3), (3, 3), (3, 3), (3, 3), (3, 3)]
        layout = layouts[len(ns_list) - 1]
        sub_w = w / layout[0]
        sub_h = h / layout[1]
        self.windows = []
        for j in range(layout[1]):
            for i in range(layout[0]):
                x = i * sub_w
                y = j * sub_h
                self.windows.append(self.image_out[int(y):int(y) + int(sub_h), int(x):int(x) + int(sub_w)])

        # create renderers
        self.renderer_list = []
        for ns in ns_list:
            self.renderer_list.append(ImageRenderer(ns))

        # subscribers
        self.capture_time = rospy.Time(0)
        self.calibrate_time = rospy.Time(0)
        self.captured_sub = rospy.Subscriber('robot_measurement', RobotMeasurement, self.captured_cb)
        self.optimized_sub = rospy.Subscriber('camera_calibration', CameraCalibration, self.calibrated_cb)

    def captured_cb(self, msg):
        with self.lock:
            self.capture_time = rospy.Time.now()
        beep([(400, 63, 0.2)])

    def calibrated_cb(self, msg):
        with self.lock:
            self.calibrate_time = rospy.Time.now()

    def loop(self):
        r = rospy.Rate(20)
        beep_time = rospy.Time(0)

        while not rospy.is_shutdown():
            try:
                r.sleep()
            except:
                print("Shutting down")
            with self.lock:
                for window, render in zip(self.windows, self.renderer_list):
                    render.render(window)

                if self.capture_time + rospy.Duration(4.0) > rospy.Time.now():
                    if self.capture_time + rospy.Duration(2.0) > rospy.Time.now():
                        # Captured checkerboards
                        self.pub.publish(self.bridge.cv2_to_imgmsg(self.image_captured, encoding="rgb8"))
                    elif self.calibrate_time + rospy.Duration(20.0) > rospy.Time.now():
                        # Succeeded optimization
                        self.pub.publish(self.bridge.cv2_to_imgmsg(self.image_optimized, encoding="rgb8"))
                        if beep_time + rospy.Duration(8.0) < rospy.Time.now():
                            beep_time = rospy.Time.now()
                            beep([(600, 63, 0.1), (800, 63, 0.1), (1000, 63, 0.3)])
                    else:
                        # Failed optimization
                        self.pub.publish(self.bridge.cv2_to_imgmsg(self.image_failed, encoding="rgb8"))
                        if beep_time + rospy.Duration(4.0) < rospy.Time.now():
                            beep_time = rospy.Time.now()
                            beep([(400, 63, 0.1), (200, 63, 0.1), (100, 63, 0.6)])

                else:
                    self.pub.publish(self.bridge.cv2_to_imgmsg(self.image_out, encoding="rgb8"))


def main():
    rospy.init_node('capture_monitor')
    args = rospy.myargv()

    a = Aggregator(args[1:])
    a.loop()


if __name__ == '__main__':
    main()
