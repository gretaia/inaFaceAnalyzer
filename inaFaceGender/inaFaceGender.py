#!/usr/bin/env python
# encoding: utf-8

# The MIT License

# Copyright (c) 2019 Ina (Zohra Rezgui & David Doukhan - http://www.ina.fr/)

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

import numpy as np
import pandas as pd
from abc import ABC, abstractmethod
from .opencv_utils import video_iterator, imread_rgb, analysisFPS2subsamp_coeff
from .face_tracking import TrackerDetector
from .face_detector import OcvCnnFacedetector
from .face_classifier import Resnet50FairFaceGRA
from .face_alignment import Dlib68FaceAlignment
from .face_preprocessing import preprocess_face
from .rect import Rect



class FaceAnalyzer(ABC):
    """
    This is an abstract class containg the common code to be used to process
    images, videos, with/without tracking
    """
    batch_len = 32

    @classmethod
    @abstractmethod
    def analyzer_cols() : pass

    def __init__(self, face_detector = None, face_classifier = None, bbox_scaling = 1.1, squarify_bbox = True, verbose = False):
        """
        Constructor
        Parameters
        ----------
        face_detector : instance of face_detector.OcvCnnFacedetector or None
            More face detections modules may be implemented
            if None, then manual bounding boxes should be provided
        bbox_scaling : float
            scaling factor to be applied to the face bounding box.
            larger bounding box may help for sex classification from face
        squarify_bbox : boolean
            if set to True, then the bounding box (manual or automatic) is set to a square
        verbose : boolean
            If True, will display several usefull intermediate images and results
        """
        # face detection system
        if face_detector is None:
            self.face_detector = OcvCnnFacedetector(padd_prct=0.)
        else:
            self.face_detector = face_detector


        # set all bounding box shapes to square
        self.squarify_bbox = squarify_bbox

        # scaling factor to be applied to face bounding boxes
        self.bbox_scaling = bbox_scaling

        # face alignment module
        self.face_alignment = Dlib68FaceAlignment()

        # Face feature extractor from aligned and detected faces
        if face_classifier is None:
            self.classifier = Resnet50FairFaceGRA()
        else:
            self.classifier = face_classifier

        # True if some verbose is required
        self.verbose = verbose




    #TODO : test in multi output
    # may be deprecated in a near future since it does not takes advantage of batches
    def classif_from_frame_and_bbox(self, frame, bbox, bbox_square, bbox_scale):

        oshape = self.classifier.input_shape[:-1]
        fa, vrb = (self.face_alignment, self.verbose)
        face_img, bbox = preprocess_face(frame, bbox, bbox_square, bbox_scale, fa, oshape, vrb)

        feats, df = self.classifier([face_img], True)
        df.insert(0, 'feats', [feats])
        df.insert(1, 'bbox', [bbox])

        return df


class GenderImage(FaceAnalyzer):
    analyzer_cols = ['frame', 'bbox', 'face_detect_conf']

    def __init__(self, **kwargs):
        if 'face_detector' not in kwargs:
            kwargs['face_detector'] = OcvCnnFacedetector()
        super().__init__(**kwargs)

    def __call__(self, img_path):
        frame = imread_rgb(img_path, self.verbose)
        return self.detect_and_classify_faces_from_frame(frame)


    def detect_and_classify_faces_from_frame(self, frame):
        lret = []
        # iterate on "generic" detect_info ??
        for bb, detect_conf in self.face_detector(frame, self.verbose):
            df = self.classif_from_frame_and_bbox(frame, bb, self.squarify_bbox, self.bbox_scaling)
            df.insert(2, 'face_detect_conf', [detect_conf])
            lret.append(df)

            if self.verbose:
                print(','.join(df.columns))
                print(df)
                print()

        if len(lret) > 0:
            return pd.concat(lret).reset_index(drop=True)
        return pd.DataFrame(columns = self.analyzer_cols + self.classifier.output_cols)

class GenderVideo(FaceAnalyzer):
    """
    This is a class regrouping all phases of a pipeline designed for gender classification from video.

    Attributes:
        face_detector: Face detection model.
        face_alignment: Face alignment model.
        gender_svm: Gender SVM classifier model.
        vgg_feature_extractor: VGGFace neural model used for feature extraction.
        threshold: quality of face detection considered acceptable, value between 0 and 1.
    """

    analyzer_cols = ['frame', 'bbox', 'face_detect_conf']

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def __call__(self, video_path, fps = None,  offset = -1):

        """
        Pipeline function for gender classification from videos without tracking.

        Parameters:
            video_path (string): Path for input video.
            subsamp_coeff (int) : only 1/subsamp_coeff frames will be processed
            offset (float) : Time in milliseconds to skip at the beginning of the video.


        Returns:
            info: A Dataframe with frame and face information (coordinates, decision function,labels..)
        """

        detector = self.face_detector
        oshape = self.classifier.input_shape[:-1]

        lbatch_img = []
        linfo = []
        ldf = []

        subsamp_coeff = 1 if fps is None else analysisFPS2subsamp_coeff(video_path, fps)

        for iframe, frame in video_iterator(video_path, subsamp_coeff=subsamp_coeff, time_unit='ms', start=min(offset, 0), verbose=self.verbose):

            for detection in detector(frame):
                if self.verbose:
                    print(detection)


                face_img, bbox = preprocess_face(frame, detection, self.squarify_bbox, self.bbox_scaling, self.face_alignment, oshape, self.verbose)

                linfo.append([iframe, tuple(bbox), detection.conf])
                lbatch_img.append(face_img)

            while len(lbatch_img) > self.batch_len:
                df = self.classifier(lbatch_img[:self.batch_len], False)
                ldf.append(df)
                lbatch_img = lbatch_img[self.batch_len:]

        if len(lbatch_img) > 0:
            df = self.classifier(lbatch_img, False)
            ldf.append(df)

        if len(ldf) == 0:
            return pd.DataFrame(None, columns=(self.analyzer_cols + self.classifier.output_cols))

        dfL = pd.DataFrame.from_records(linfo, columns = self.analyzer_cols)
        dfR = pd.concat(ldf).reset_index(drop=True)
        return pd.concat([dfL, dfR], axis = 1)


    def pred_from_vid_and_bblist(self, vidsrc, lbox, fps=None, start_frame=0):
        ldf = []

        subsamp_coeff = 1 if fps is None else analysisFPS2subsamp_coeff(vidsrc, fps)

        for (iframe, frame), bbox in zip(video_iterator(vidsrc, subsamp_coeff=subsamp_coeff, start=start_frame, verbose=self.verbose),lbox):

            if not isinstance(bbox, Rect):
                bbox = Rect(*bbox)

            df = self.classif_from_frame_and_bbox(frame, bbox, self.squarify_bbox, self.bbox_scaling)

            ldf.append(df)

            if self.verbose:
                print(df.drop('feats', axis=1))
                print()
        assert len(ldf) == len(lbox), '%d bounding box provided, and only %d frames processed' % (len(lbox), len(ldf))

        df = pd.concat(ldf).reset_index(drop=True)
        return np.concatenate(df.feats), df.drop('feats', axis=1)


class GenderTracking(FaceAnalyzer):
    analyzer_cols = ['frame', 'bbox', 'face_id', 'face_detect_conf', 'face_track_conf']
    def __init__(self, detection_period, **kwargs):
        super().__init__(**kwargs)
        self.detection_period = detection_period

    def __call__(self, video_path, fps = None,  offset = -1):

        """
        Pipeline function for gender classification from videos without tracking.

        Parameters:
            video_path (string): Path for input video.
            subsamp_coeff (int) : only 1/subsamp_coeff frames will be processed
            offset (float) : Time in milliseconds to skip at the beginning of the video.


        Returns:
            info: A Dataframe with frame and face information (coordinates, decision function,labels..)
        """

        detector = TrackerDetector(self.face_detector, self.detection_period)

        oshape = self.classifier.input_shape[:-1]

        lbatch_img = []
        linfo = []
        ldf = []

        subsamp_coeff = 1 if fps is None else analysisFPS2subsamp_coeff(video_path, fps)

        for iframe, frame in video_iterator(video_path, subsamp_coeff=subsamp_coeff, time_unit='ms', start=min(offset, 0), verbose=self.verbose):

            for detection in detector(frame):
                if self.verbose:
                    print(detection)

                face_img, bbox = preprocess_face(frame, detection, self.squarify_bbox, self.bbox_scaling, self.face_alignment, oshape, self.verbose)

                linfo.append([iframe, tuple(bbox), detection.face_id, detection.detect_conf, detection.track_conf])
                lbatch_img.append(face_img)

            while len(lbatch_img) > self.batch_len:
                df = self.classifier(lbatch_img[:self.batch_len], False)
                ldf.append(df)
                lbatch_img = lbatch_img[self.batch_len:]

        if len(lbatch_img) > 0:
            df = self.classifier(lbatch_img, False)
            ldf.append(df)

        if len(ldf) == 0:
            df = pd.DataFrame(None, columns=(self.analyzer_cols + self.classifier.output_cols))
        else:
            dfL = pd.DataFrame.from_records(linfo, columns = self.analyzer_cols)
            dfR = pd.concat(ldf).reset_index(drop=True)
            df = pd.concat([dfL, dfR], axis = 1)

        return self.classifier.average_results(df)
