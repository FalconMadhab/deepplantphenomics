from . import layers
from . import loaders
from . import definitions
import numpy as np
import tensorflow as tf
import os
import json
import datetime
import warnings
import copy
from abc import ABC, abstractmethod
import math


class DPPModel(ABC):
    """
    The DPPModel class represents a model which can either be trained, or loaded from an existing checkpoint file. It
    provides common functionality and parameters for models of all problem types. Subclasses of DPPModel implement any
    changes and extra methods required to support that particular problem.
    """

    # Operation settings
    _problem_type = definitions.ProblemType.CLASSIFICATION
    _loss_fn = 'softmax cross entropy'
    _with_patching = False
    _has_trained = False
    _save_checkpoints = None
    _save_dir = None
    _validation = True
    _testing = True
    _hyper_param_search = False

    # Input options
    _total_classes = 0
    _total_raw_samples = 0
    _total_training_samples = 0
    _total_validation_samples = 0
    _total_testing_samples = 0

    _image_width = None
    _image_height = None
    _image_width_original = None
    _image_height_original = None
    _image_depth = None
    _patch_height = None
    _patch_width = None
    _resize_bbox_coords = False

    _crop_or_pad_images = False
    _resize_images = False

    _processed_images_dir = './DPP-Processed'

    # Supported implementations for various network components
    _supported_optimizers = ['adam', 'adagrad', 'adadelta', 'sgd', 'sgd_momentum']
    _supported_weight_initializers = ['normal', 'xavier']
    _supported_activation_functions = ['relu', 'tanh', 'lrelu', 'selu']
    _supported_pooling_types = ['max', 'avg']
    _supported_loss_fns = ['softmax cross entropy', 'l2', 'l1', 'smooth l1', 'log loss', 'sigmoid cross entropy',
                           'yolo']
    _supported_predefined_models = ['vgg-16', 'alexnet', 'yolov2', 'xsmall', 'small', 'medium', 'large']

    # Augmentation options
    _augmentation_flip_horizontal = False
    _augmentation_flip_vertical = False
    _augmentation_crop = False
    _crop_amount = 0.75
    _augmentation_contrast = False
    _augmentation_rotate = False
    _rotate_crop_borders = False
    # The list of valid augmentations defaults to including all possible augmentations
    _valid_augmentations = [definitions.AugmentationType.FLIP_HOR,
                            definitions.AugmentationType.FLIP_VER,
                            definitions.AugmentationType.CROP,
                            definitions.AugmentationType.CONTRAST_BRIGHT,
                            definitions.AugmentationType.ROTATE]

    # Dataset storage
    _all_ids = None

    _all_images = None
    _train_images = None
    _test_images = None
    _val_images = None

    _all_labels = None
    _train_labels = None
    _test_labels = None
    _val_labels = None
    _split_labels = True

    _images_only = False

    _raw_image_files = None
    _raw_labels = None

    _raw_test_image_files = None
    _raw_train_image_files = None
    _raw_val_image_files = None
    _raw_test_labels = None
    _raw_train_labels = None
    _raw_val_labels = None

    _all_moderation_features = None
    _has_moderation = False
    _moderation_features_size = None
    _train_moderation_features = None
    _test_moderation_features = None
    _val_moderation_features = None

    _training_augmentation_images = None
    _training_augmentation_labels = None

    # Network internal representation
    _session = None
    _graph = None
    _graph_ops = {}
    _layers = []
    _global_epoch = 0

    _num_layers_norm = 0
    _num_layers_conv = 0
    _num_layers_upsample = 0
    _num_layers_pool = 0
    _num_layers_fc = 0
    _num_layers_dropout = 0
    _num_layers_batchnorm = 0

    # Network options
    _batch_size = 1
    _test_split = 0.10
    _validation_split = 0.10
    _maximum_training_batches = None
    _reg_coeff = None
    _optimizer = 'adam'
    _weight_initializer = 'xavier'

    _learning_rate = 0.001
    _lr_decay_factor = None
    _epochs_per_decay = None
    _lr_decay_epochs = None

    # Wrapper options
    _debug = None
    _load_from_saved = None
    _tb_dir = None
    _queue_capacity = 50
    _report_rate = None

    # Multi-threading
    _num_threads = 1
    _coord = None
    _threads = None

    def __init__(self, debug=False, load_from_saved=False, save_checkpoints=True, initialize=True, tensorboard_dir=None,
                 report_rate=100, save_dir=None):
        """
        Create a new model object

        :param debug: If True, debug messages are printed to the console.
        :param load_from_saved: Optionally, pass the name of a directory containing the checkpoint file.
        :param save_checkpoints: If True, trainable parameters will be saved at intervals during training.
        :param initialize: If False, a new Tensorflow session will not be initialized with the instance.
        :param tensorboard_dir: Optionally, provide the path to your Tensorboard logs directory.
        :param report_rate: Set the frequency at which progress is reported during training (also the rate at which new
        timepoints are recorded to Tensorboard).
        """
        self._debug = debug
        self._load_from_saved = load_from_saved
        self._tb_dir = tensorboard_dir
        self._report_rate = report_rate
        self._save_checkpoints = save_checkpoints
        self._save_dir = save_dir

        # Add the run level to the tensorboard path
        if self._tb_dir is not None:
            self._tb_dir = "{0}/{1}".format(self._tb_dir, datetime.datetime.now().strftime("%d%B%Y%I:%M%p"))

        if initialize:
            self.__log('TensorFlow loaded...')

            self.__reset_graph()
            self.__reset_session()

    def __log(self, message):
        if self._debug:
            print('{0}: {1}'.format(datetime.datetime.now().strftime("%I:%M%p"), message))

    def __last_layer(self):
        return self._layers[-1]

    def __last_layer_outputs_volume(self):
        return isinstance(self.__last_layer().output_size, (list,))

    def __first_layer(self):
        return next(layer for layer in self._layers if
                    isinstance(layer, layers.convLayer) or isinstance(layer, layers.fullyConnectedLayer))

    def __reset_session(self):
        self._session = tf.Session(graph=self._graph)

    def __reset_graph(self):
        self._graph = tf.Graph()

    def __initialize_queue_runners(self):
        self.__log('Initializing queue runners...')
        self._coord = tf.train.Coordinator()
        self._threads = tf.train.start_queue_runners(sess=self._session, coord=self._coord)

    def set_number_of_threads(self, num_threads):
        """Set number of threads for input queue runners and preprocessing tasks"""
        if not isinstance(num_threads, int):
            raise TypeError("num_threads must be an int")
        if num_threads <= 0:
            raise ValueError("num_threads must be positive")

        self._num_threads = num_threads

    def set_processed_images_dir(self, im_dir):
        """Set the directory for storing processed images when pre-processing is used"""
        if not isinstance(im_dir, str):
            raise TypeError("im_dir must be a str")

        self._processed_images_dir = im_dir

    def set_batch_size(self, size):
        """Set the batch size"""
        if not isinstance(size, int):
            raise TypeError("size must be an int")
        if size <= 0:
            raise ValueError("size must be positive")

        self._batch_size = size
        self._queue_capacity = size * 5

    def set_train_test_split(self, ratio):
        """DEPRECATED
        Set a ratio for the total number of samples to use as a training set, using the rest of the samples for testing
        (i.e. no validation samples)"""
        if not isinstance(ratio, float) and ratio != 1:
            raise TypeError("ratio must be a float or 1")
        if ratio <= 0 or ratio > 1:
            raise ValueError("ratio must be between 0 and 1")
        warnings.warn("set_train_test_split() is deprecated and will be removed soon. " +
                      "Use set_test_split() and set_validation_split() instead. See docs for more information.")

        self._test_split = 1 - ratio
        if ratio == 1 or ratio is None:
            self._testing = False
        else:
            self._testing = True
        self._validation = False
        self._validation_split = 0

    def set_test_split(self, ratio):
        """Set a ratio for the total number of samples to use as a testing set"""
        if not isinstance(ratio, float) and ratio != 0:
            raise TypeError("ratio must be a float or 0")
        if ratio < 0 or ratio > 1:
            raise ValueError("ratio must be between 0 and 1")

        if ratio == 0 or ratio is None:
            self._testing = False
            ratio = 0
        else:
            self._testing = True
        self._test_split = ratio
        if self._test_split + self._validation_split > 0.5:
            warnings.warn('WARNING: Less than 50% of data is being used for training. ' +
                          '({test}% testing and {val}% validation)'.format(test=int(self._test_split * 100),
                                                                           val=int(self._validation_split * 100)))

    def set_validation_split(self, ratio):
        """Set a ratio for the total number of samples to use as a validation set"""
        if not isinstance(ratio, float) and ratio != 0:
            raise TypeError("ratio must be a float or 0")
        if ratio < 0 or ratio > 1:
            raise ValueError("ratio must be between 0 and 1")

        if ratio == 0 or ratio is None:
            self._validation = False
            ratio = 0
        else:
            self._validation = True
        self._validation_split = ratio
        if self._test_split + self._validation_split > 0.5:
            warnings.warn('WARNING: Less than 50% of data is being used for training. ' +
                          '({test}% testing and {val}% validation)'.format(test=int(self._test_split * 100),
                                                                           val=int(self._validation_split * 100)))

    def set_maximum_training_epochs(self, epochs):
        """Set the max number of training epochs"""
        if not isinstance(epochs, int):
            raise TypeError("epochs must be an int")
        if epochs <= 0:
            raise ValueError("epochs must be positive")

        self._maximum_training_batches = epochs

    def set_learning_rate(self, rate):
        """Set the initial learning rate"""
        if not isinstance(rate, float):
            raise TypeError("rate must be a float")
        if rate <= 0:
            raise ValueError("rate must be positive")

        self._learning_rate = rate

    def set_crop_or_pad_images(self, crop_or_pad):
        """Apply padding or cropping images to, which is required if the dataset has images of different sizes"""
        if not isinstance(crop_or_pad, bool):
            raise TypeError("crop_or_pad must be a bool")

        self._crop_or_pad_images = crop_or_pad

    def set_resize_images(self, resize):
        """Up-sample or down-sample images to specified size"""
        if not isinstance(resize, bool):
            raise TypeError("resize must be a bool")

        self._resize_images = resize

    def set_augmentation_flip_horizontal(self, flip):
        """Randomly flip training images horizontally"""
        if not isinstance(flip, bool):
            raise TypeError("flip must be a bool")
        if definitions.AugmentationType.FLIP_HOR not in self._valid_augmentations:
            raise RuntimeError("Flip augmentations are incompatible with the current problem type")

        self._augmentation_flip_horizontal = flip

    def set_augmentation_flip_vertical(self, flip):
        """Randomly flip training images vertically"""
        if not isinstance(flip, bool):
            raise TypeError("flip must be a bool")
        if definitions.AugmentationType.FLIP_VER not in self._valid_augmentations:
            raise RuntimeError("Flip augmentations are incompatible with the current problem type")

        self._augmentation_flip_vertical = flip

    def set_augmentation_crop(self, resize, crop_ratio=0.75):
        """Randomly crop images during training, and crop images to center during testing"""
        if not isinstance(resize, bool):
            raise TypeError("resize must be a bool")
        if not isinstance(crop_ratio, float):
            raise TypeError("crop_ratio must be a float")
        if crop_ratio <= 0 or crop_ratio > 1:
            raise ValueError("crop_ratio must be in (0, 1]")
        if definitions.AugmentationType.CROP not in self._valid_augmentations:
            raise RuntimeError("Crop augmentations are incompatible with the current problem type")

        self._augmentation_crop = resize
        self._crop_amount = crop_ratio

    def set_augmentation_brightness_and_contrast(self, contr):
        """Randomly adjust contrast and/or brightness on training images"""
        if not isinstance(contr, bool):
            raise TypeError("contr must be a bool")
        if definitions.AugmentationType.CONTRAST_BRIGHT not in self._valid_augmentations:
            raise RuntimeError("Contrast and brightness augmentations are incompatible with the current problem type")

        self._augmentation_contrast = contr

    def set_augmentation_rotation(self, rot, crop_borders=False):
        """Randomly rotate training images"""
        if not isinstance(rot, bool):
            raise TypeError("rot must be a bool")
        if not isinstance(crop_borders, bool):
            raise TypeError("crop_borders must be a bool")
        if definitions.AugmentationType.ROTATE not in self._valid_augmentations:
            raise RuntimeError("Rotation augmentations are incompatible with the current problem type")

        self._augmentation_rotate = rot
        self._rotate_crop_borders = crop_borders

    def set_regularization_coefficient(self, lamb):
        """Set lambda for L2 weight decay"""
        if not isinstance(lamb, float):
            raise TypeError("lamb must be a float")
        if lamb <= 0:
            raise ValueError("lamb must be positive")

        self._reg_coeff = lamb

    def set_learning_rate_decay(self, decay_factor, epochs_per_decay):
        """Set learning rate decay"""
        if not isinstance(decay_factor, float):
            raise TypeError("decay_factor must be a float")
        if decay_factor <= 0:
            raise ValueError("decay_factor must be positive")
        if not isinstance(epochs_per_decay, int):
            raise TypeError("epochs_per_day must be an int")
        if epochs_per_decay <= 0:
            raise ValueError("epochs_per_day must be positive")

        self._lr_decay_factor = decay_factor
        self._epochs_per_decay = epochs_per_decay

    def set_optimizer(self, optimizer):
        """Set the optimizer to use"""
        if not isinstance(optimizer, str):
            raise TypeError("optimizer must be a str")
        if optimizer.lower() in self._supported_optimizers:
            optimizer = optimizer.lower()
        else:
            raise ValueError("'" + optimizer + "' is not one of the currently supported optimizers. Choose one of " +
                             " ".join("'" + x + "'" for x in self._supported_optimizers))

        self._optimizer = optimizer

    def set_loss_function(self, loss_fn):
        """Set the loss function to use"""
        if not isinstance(loss_fn, str):
            raise TypeError("loss_fn must be a str")
        loss_fn = loss_fn.lower()

        if loss_fn not in self._supported_loss_fns:
            raise ValueError("'" + loss_fn + "' is not one of the currently supported loss functions for the " +
                             "current problem type. Make sure you have the correct problem type set with " +
                             "DPPModel.set_problem_type() first, or choose one of " +
                             " ".join("'" + x + "'" for x in self._supported_loss_fns))

        self._loss_fn = loss_fn

    def set_weight_initializer(self, initializer):
        """Set the initialization scheme used by convolutional and fully connected layers"""
        if not isinstance(initializer, str):
            raise TypeError("initializer must be a str")
        initializer = initializer.lower()
        if initializer not in self._supported_weight_initializers:
            raise ValueError("'" + initializer + "' is not one of the currently supported weight initializers." +
                             " Choose one of: " + " ".join("'"+x+"'" for x in self._supported_weight_initializers))

        self._weight_initializer = initializer

    def set_image_dimensions(self, image_height, image_width, image_depth):
        """Specify the image dimensions for images in the dataset (depth is the number of channels)"""
        if not isinstance(image_height, int):
            raise TypeError("image_height must be an int")
        if image_height <= 0:
            raise ValueError("image_height must be positive")
        if not isinstance(image_width, int):
            raise TypeError("image_width must be an int")
        if image_width <= 0:
            raise ValueError("image_width must be positive")
        if not isinstance(image_depth, int):
            raise TypeError("image_depth must be an int")
        if image_depth <= 0:
            raise ValueError("image_depth must be positive")

        self._image_width = image_width
        self._image_height = image_height
        self._image_depth = image_depth

    def set_original_image_dimensions(self, image_height, image_width):
        """
        Specify the original size of the image, before resizing.
        This is only needed in special cases, for instance, if you are resizing input images but using image coordinate
        labels which reference the original size.
        """
        if not isinstance(image_height, int):
            raise TypeError("image_height must be an int")
        if image_height <= 0:
            raise ValueError("image_height must be positive")
        if not isinstance(image_width, int):
            raise TypeError("image_width must be an int")
        if image_width <= 0:
            raise ValueError("image_width must be positive")

        self._image_width_original = image_width
        self._image_height_original = image_height

    def add_moderation_features(self, moderation_features):
        """Specify moderation features for examples in the dataset"""
        self._has_moderation = True
        self._moderation_features_size = moderation_features.shape[1]
        self._all_moderation_features = moderation_features

    def set_patch_size(self, height, width):
        if not isinstance(height, int):
            raise TypeError("height must be an int")
        if height <= 0:
            raise ValueError("height must be positive")
        if not isinstance(width, int):
            raise TypeError("width must be an int")
        if width <= 0:
            raise ValueError("width must be positive")

        self._patch_height = height
        self._patch_width = width
        self._with_patching = True

    def __add_layers_to_graph(self):
        """
        Adds the layers in self.layers to the computational graph.

        Currently __assemble_graph is doing too many things, so this is needed as a separate function so that other
        functions such as load_state can add layers to the graph without performing everything else in assemble_graph
        """
        for layer in self._layers:
            if callable(getattr(layer, 'add_to_graph', None)):
                layer.add_to_graph()

    def _graph_parse_data(self):
        """
        Add graph components that parse the input images and labels into tensors and split them into training,
        validation, and testing sets
        """
        if self._raw_test_labels is not None:
            # currently think of moderation features as None so they are passed in hard-coded
            self.__parse_dataset(self._raw_train_image_files, self._raw_train_labels, None,
                                 self._raw_test_image_files, self._raw_test_labels, None,
                                 self._raw_val_image_files, self._raw_val_labels, None)
        elif self._images_only:
            self.__parse_images(self._raw_image_files)
        else:
            # Split the data into training, validation, and testing sets. If there is no validation set or no moderation
            # features being used they will be returned as 0 (for validation) or None (for moderation features)
            train_images, train_labels, train_mf, \
                    test_images, test_labels, test_mf, \
                    val_images, val_labels, val_mf, = \
                    loaders.split_raw_data(self._raw_image_files, self._raw_labels, self._test_split,
                                           self._validation_split, self._all_moderation_features,
                                           self._training_augmentation_images, self._training_augmentation_labels,
                                           self._split_labels)
            # Parse the images and set the appropriate environment variables
            self.__parse_dataset(train_images, train_labels, train_mf,
                                 test_images, test_labels, test_mf,
                                 val_images, val_labels, val_mf)

    def _graph_add_optimizer(self):
        """
        Adds graph components for setting and running an optimization operation
        :return: The optimizer's gradients, variables, and the global_grad_norm
        """
        # Identify which optimizer we are using
        if self._optimizer == 'adagrad':
            self._graph_ops['optimizer'] = tf.train.AdagradOptimizer(self._learning_rate)
            self.__log('Using Adagrad optimizer')
        elif self._optimizer == 'adadelta':
            self._graph_ops['optimizer'] = tf.train.AdadeltaOptimizer(self._learning_rate)
            self.__log('Using adadelta optimizer')
        elif self._optimizer == 'sgd':
            self._graph_ops['optimizer'] = tf.train.GradientDescentOptimizer(self._learning_rate)
            self.__log('Using SGD optimizer')
        elif self._optimizer == 'adam':
            self._graph_ops['optimizer'] = tf.train.AdamOptimizer(self._learning_rate)
            self.__log('Using Adam optimizer')
        elif self._optimizer == 'sgd_momentum':
            self._graph_ops['optimizer'] = tf.train.MomentumOptimizer(self._learning_rate, 0.9, use_nesterov=True)
            self.__log('Using SGD with momentum optimizer')
        else:
            warnings.warn('Unrecognized optimizer requested')
            exit()

        # Compute gradients, clip them, the apply the clipped gradients
        # This is broken up so that we can add gradients to tensorboard
        # need to make the 5.0 an adjustable hyperparameter
        gradients, variables = zip(*self._graph_ops['optimizer'].compute_gradients(self._graph_ops['cost']))
        gradients, global_grad_norm = tf.clip_by_global_norm(gradients, 5.0)
        self._graph_ops['optimizer'] = self._graph_ops['optimizer'].apply_gradients(zip(gradients, variables))

        return gradients, variables, global_grad_norm

    def _graph_tensorboard_summary(self, l2_cost, gradients, variables, global_grad_norm):
        """
        Adds graph components related to outputting losses and other summary variables to Tensorboard. This covers
        common outputs across every problem type.
        :param l2_cost: ...
        :param gradients: ...
        :param global_grad_norm: ...
        """
        if self._tb_dir is not None:
            self.__log('Creating Tensorboard summaries...')

            # Summaries for any problem type
            tf.summary.scalar('train/loss', self._graph_ops['cost'], collections=['custom_summaries'])
            tf.summary.scalar('train/learning_rate', self._learning_rate, collections=['custom_summaries'])
            tf.summary.scalar('train/l2_loss', l2_cost, collections=['custom_summaries'])
            filter_summary = self.__get_weights_as_image(self.__first_layer().weights)
            tf.summary.image('filters/first', filter_summary, collections=['custom_summaries'])

            # Summaries for each layer
            for layer in self._layers:
                if hasattr(layer, 'name') and not isinstance(layer, layers.batchNormLayer):
                    tf.summary.histogram('weights/' + layer.name, layer.weights, collections=['custom_summaries'])
                    tf.summary.histogram('biases/' + layer.name, layer.biases, collections=['custom_summaries'])

                    # At one point the graph would hang on session.run(graph_ops['merged']) inside of begin_training
                    # and it was found that if you commented the below line then the code wouldn't hang. Never
                    # fully understood why, as it only happened if you tried running with train/test and no
                    # validation. But after adding more features and just randomly trying to uncomment the below
                    # line to see if it would work, it appears to now be working, but still don't know why...
                    tf.summary.histogram('activations/' + layer.name, layer.activations,
                                         collections=['custom_summaries'])

            # Summaries for gradients
            # We use variables[index].name[:-2] because variables[index].name will have a ':0' at the end of
            # the name and tensorboard does not like this so we remove it with the [:-2]
            # We also currently seem to get None's for gradients when performing a hyper-parameter search
            # and as such it is simply left out for hyper-param searches, needs to be fixed
            if not self._hyper_param_search:
                for index, grad in enumerate(gradients):
                    tf.summary.histogram("gradients/" + variables[index].name[:-2], gradients[index],
                                         collections=['custom_summaries'])

                tf.summary.histogram("gradient_global_norm/", global_grad_norm, collections=['custom_summaries'])

            self._graph_ops['merged'] = tf.summary.merge_all(key='custom_summaries')

    @abstractmethod
    def __assemble_graph(self):
        """
        Constructs the Tensorflow graph that defines the network. This includes splitting the input data into
        train/validation/test partitions, parsing it into Tensors, performing the forward pass and optimization steps,
        returning test and validation losses, and outputting losses and other variables to Tensorboard if necessary.
        Parts of the graph should be exposed by adding graph nodes to the `_graph_ops` variable; which nodes and their
        names will vary with the problem type.
        """
        pass

    @abstractmethod
    def begin_training(self, return_test_loss=False):
        """
        Initialize the network and either run training to the specified max epoch, or load trainable variables. The
        full test accuracy is calculated immediately afterward and the trainable parameters are saved before the
        session is shut down. Before calling this function, the images and labels should be loaded, as well as all
        relevant hyper-parameters.
        """
        pass

    def begin_training_with_hyperparameter_search(self, l2_reg_limits=None, lr_limits=None, num_steps=3):
        """
        Performs grid-based hyper-parameter search given the ranges passed. Parameters are optional.

        :param l2_reg_limits: array representing a range of L2 regularization coefficients in the form [low, high]
        :param lr_limits: array representing a range of learning rates in the form [low, high]
        :param num_steps: the size of the grid. Larger numbers are exponentially slower.
        """
        self._hyper_param_search = True

        base_tb_dir = self._tb_dir

        unaltered_image_height = self._image_height
        unaltered_image_width = self._image_width
        unaltered_epochs = self._maximum_training_batches

        if l2_reg_limits is None:
            all_l2_reg = [self._reg_coeff]
        else:
            step_size = (l2_reg_limits[1] - l2_reg_limits[0]) / np.float32(num_steps-1)
            all_l2_reg = np.arange(l2_reg_limits[0], l2_reg_limits[1], step_size)
            all_l2_reg = np.append(all_l2_reg, l2_reg_limits[1])

        if lr_limits is None:
            all_lr = [self._learning_rate]
        else:
            step_size = (lr_limits[1] - lr_limits[0]) / np.float32(num_steps-1)
            all_lr = np.arange(lr_limits[0], lr_limits[1], step_size)
            all_lr = np.append(all_lr, lr_limits[1])

        all_loss_results = np.empty([len(all_l2_reg), len(all_lr)])

        for i, current_l2 in enumerate(all_l2_reg):
            for j, current_lr in enumerate(all_lr):
                self.__log('HYPERPARAMETER SEARCH: Doing l2reg=%f, lr=%f' % (current_l2, current_lr))

                # Make a new graph, associate a new session with it.
                self.__reset_graph()
                self.__reset_session()

                self._learning_rate = current_lr
                self._reg_coeff = current_l2

                # Set calculated variables back to their unaltered form
                self._image_height = unaltered_image_height
                self._image_width = unaltered_image_width
                self._maximum_training_batches = unaltered_epochs

                # Reset the reg. coef. for all fc layers.
                with self._graph.as_default():
                    for layer in self._layers:
                        if isinstance(layer, layers.fullyConnectedLayer):
                            layer.regularization_coefficient = current_l2

                if base_tb_dir is not None:
                    self._tb_dir = base_tb_dir + '_lr:' + current_lr.astype('str') + '_l2:' + current_l2.astype('str')

                try:
                    current_loss = self.begin_training(return_test_loss=True)
                    all_loss_results[i][j] = current_loss
                except Exception as e:
                    self.__log('HYPERPARAMETER SEARCH: Run threw an exception, this result will be NaN.')
                    print("Exception message: "+str(e))
                    all_loss_results[i][j] = np.nan

        self.__log('Finished hyperparameter search, failed runs will appear as NaN.')
        self.__log('All l2 coef. tested:')
        self.__log('\n'+np.array2string(np.transpose(all_l2_reg)))
        self.__log('All learning rates tested:')
        self.__log('\n'+np.array2string(all_lr))
        self.__log('Loss/error grid:')
        self.__log('\n'+np.array2string(all_loss_results, precision=4))

    @abstractmethod
    def compute_full_test_accuracy(self):
        """
        Prints to console and returns accuracy and loss statistics for the trained network. The applicable statistics
        will depend on the problem type.
        """
        pass

    def shut_down(self):
        """Stop all queues and end session. The model cannot be used anymore after a shut down is completed."""
        self.__log('Shutdown requested, ending session...')

        self._coord.request_stop()
        self._coord.join(self._threads)

        self._session.close()

    def __get_weights_as_image(self, kernel, size=None):
        """Filter visualization, adapted with permission from https://gist.github.com/kukuruza/03731dc494603ceab0c5"""
        with self._graph.as_default():
            pad = 1
            grid_x = 4

            # pad x and y
            x1 = tf.pad(kernel, tf.constant([[pad, 0], [pad, 0], [0, 0], [0, 0]]))

            # when kernel is dynamically shaped at runtime it has [?,?,?,?] dimensions which result in None's
            # thus size needs to be passed in so we have actual dimensions to work with (this is mostly from the
            # upsampling layer) and grid_y will be determined by batch size as we want to see each img in the batch
            # However, for visualizing the weights we wont pass in a size parameter and as a result we need to
            # compute grid_y based off what is passed in and not the batch size because we want to see the
            # convolution grid for each layer, not each batch.
            if size is not None:
                # this is when visualizing the actual images
                grid_y = int(np.ceil(self._batch_size / 4))
                # x and y dimensions, w.r.t. padding
                y = size[1] + pad
                x = size[2] + pad
                num_channels = size[-1]
            else:
                # this is when visualizing the weights
                grid_y = (kernel.get_shape().as_list()[-1] / 4)
                # x and y dimensions, w.r.t. padding
                y = kernel.get_shape()[0] + pad
                x = kernel.get_shape()[1] + pad
                num_channels = kernel.get_shape().as_list()[2]

            # pack into image with proper dimensions for tf.image_summary
            x2 = tf.transpose(x1, (3, 0, 1, 2))
            x3 = tf.reshape(x2, tf.stack([grid_x, y * grid_y, x, num_channels]))
            x4 = tf.transpose(x3, (0, 2, 1, 3))
            x5 = tf.reshape(x4, tf.stack([1, x * grid_x, y * grid_y, num_channels]))
            x6 = tf.transpose(x5, (2, 1, 3, 0))
            x7 = tf.transpose(x6, (3, 0, 1, 2))

            # scale to [0, 1]
            x_min = tf.reduce_min(x7)
            x_max = tf.reduce_max(x7)
            x8 = (x7 - x_min) / (x_max - x_min)

        return x8

    def save_state(self, directory=None):
        """Save all trainable variables as a checkpoint in the current working path"""
        self.__log('Saving parameters...')

        if directory is None:
            state_dir = './saved_state'
        else:
            state_dir = directory + '/saved_state'

        if not os.path.isdir(state_dir):
            os.mkdir(state_dir)

        with self._graph.as_default():
            saver = tf.train.Saver(tf.trainable_variables())
            saver.save(self._session, state_dir + '/tfhSaved')

        self._has_trained = True

    def load_state(self):
        """
        Load all trainable variables from a checkpoint file specified from the load_from_saved parameter in the
        class constructor.
        """
        if not self._has_trained:
            self.__add_layers_to_graph()

        if self._load_from_saved is not False:
            self.__log('Loading from checkpoint file...')

            with self._graph.as_default():
                saver = tf.train.Saver(tf.trainable_variables())
                saver.restore(self._session, tf.train.latest_checkpoint(self._load_from_saved))

            self._has_trained = True
        else:
            warnings.warn('Tried to load state with no file given. Make sure load_from_saved is set in constructor.')
            exit()

    def __set_learning_rate(self):
        if self._lr_decay_factor is not None:
            # needs to be reexamined
            self._lr_decay_epochs = self._epochs_per_decay * (self._total_training_samples * (1 - self._test_split))
            self._learning_rate = tf.train.exponential_decay(self._learning_rate,
                                                             self._global_epoch,
                                                             self._lr_decay_epochs,
                                                             self._lr_decay_factor,
                                                             staircase=True)

    def forward_pass(self, x, deterministic=False, moderation_features=None):
        """
        Perform a forward pass of the network with an input tensor. In general, this is only used when the model is
        integrated into a Tensorflow graph. See forward_pass_with_file_inputs for a version that returns network
        outputs detached from a graph.

        :param x: input tensor where the first dimension is batch
        :param deterministic: if True, performs inference-time operations on stochastic layers e.g. DropOut layers
        :param moderation_features: ???
        :return: output tensor where the first dimension is batch
        """
        with self._graph.as_default():
            for layer in self._layers:
                if isinstance(layer, layers.moderationLayer) and moderation_features is not None:
                    x = layer.forward_pass(x, deterministic, moderation_features)
                else:
                    x = layer.forward_pass(x, deterministic)

        return x

    @abstractmethod
    def forward_pass_with_file_inputs(self, x):
        """
        Get network outputs with a list of filenames of images as input. Handles all the loading and batching
        automatically, so the size of the input can exceed the available memory without any problems.

        :param x: list of strings representing image filenames
        :return: ndarray representing network outputs corresponding to inputs in the same order
        """
        pass

    @abstractmethod
    def forward_pass_with_interpreted_outputs(self, x):
        """
        Performs the forward pass of the network and then interprets the raw outputs into the desired format based on
        the problem type and whether patching is being used.

        :param x: list of strings representing image filenames
        :return: ndarray representing network outputs corresponding to inputs in the same order
        """
        pass

    def add_input_layer(self):
        """Add an input layer to the network"""
        if len(self._layers) > 0:
            raise RuntimeError("Trying to add an input layer to a model that already contains other layers. " +
                               " The input layer need to be the first layer added to the model.")

        self.__log('Adding the input layer...')

        apply_crop = (self._augmentation_crop and self._all_images is None and self._train_images is None)

        if apply_crop:
            size = [self._batch_size, int(self._image_height * self._crop_amount),
                    int(self._image_width * self._crop_amount), self._image_depth]
        else:
            size = [self._batch_size, self._image_height, self._image_width, self._image_depth]

        if self._with_patching:
            size = [self._batch_size, self._patch_height, self._patch_width, self._image_depth]

        with self._graph.as_default():
            layer = layers.inputLayer(size)

        self._layers.append(layer)

    def add_moderation_layer(self):
        """Add a moderation layer to the network"""
        self.__log('Adding moderation layer...')

        reshape = self.__last_layer_outputs_volume()

        feat_size = self._moderation_features_size

        with self._graph.as_default():
            layer = layers.moderationLayer(copy.deepcopy(
                self.__last_layer().output_size), feat_size, reshape, self._batch_size)

        self._layers.append(layer)

    def add_convolutional_layer(self, filter_dimension, stride_length, activation_function):
        """
        Add a convolutional layer to the model.

        :param filter_dimension: array of dimensions in the format [x_size, y_size, depth, num_filters]
        :param stride_length: convolution stride length
        :param activation_function: the activation function to apply to the activation map
        """
        if len(self._layers) < 1:
            raise RuntimeError("A convolutional layer cannot be the first layer added to the model. " +
                               "Add an input layer with DPPModel.add_input_layer() first.")
        try:
            # try to iterate through filter_dimension, checking it has 4 ints
            idx = 0
            for idx, dim in enumerate(filter_dimension):
                if not (isinstance(dim, int) or isinstance(dim, np.int64)):  # np.int64 numpy default int
                    raise TypeError()
            if idx != 3:
                raise TypeError()
        except Exception:
            raise TypeError("filter_dimension must be a list or array of 4 ints")

        if not isinstance(stride_length, int):
            raise TypeError("stride_length must be an int")
        if stride_length <= 0:
            raise ValueError("stride_length must be positive")
        if not isinstance(activation_function, str):
            raise TypeError("activation_function must be a str")
        activation_function = activation_function.lower()
        if activation_function not in self._supported_activation_functions:
            raise ValueError("'" + activation_function + "' is not one of the currently supported activation " +
                             "functions. Choose one of: " +
                             " ".join("'"+x+"'" for x in self._supported_activation_functions))

        self._num_layers_conv += 1
        layer_name = 'conv%d' % self._num_layers_conv
        self.__log('Adding convolutional layer %s...' % layer_name)

        with self._graph.as_default():
            layer = layers.convLayer(layer_name,
                                     copy.deepcopy(self.__last_layer().output_size),
                                     filter_dimension,
                                     stride_length,
                                     activation_function,
                                     self._weight_initializer)

        self.__log('Filter dimensions: {0} Outputs: {1}'.format(filter_dimension, layer.output_size))

        self._layers.append(layer)

    def add_upsampling_layer(self, filter_size, num_filters, upscale_factor=2,
                             activation_function=None, regularization_coefficient=None):
        """
        Add a 2d upsampling layer to the model.

        :param filter_size: an int, representing the dimension of the square filter to be used
        :param num_filters: an int, representing the number of filters that will be outputted (the output tensor depth)
        :param upscale_factor: an int, or tuple of ints, representing the upsampling factor for rows and columns
        :param activation_function: the activation function to apply to the activation map
        :param regularization_coefficient: optionally, an L2 decay coefficient for this layer (overrides the coefficient
         set by set_regularization_coefficient)
        """
        self._num_layers_upsample += 1
        layer_name = 'upsample%d' % self._num_layers_upsample
        self.__log('Adding upsampling layer %s...' % layer_name)

        if regularization_coefficient is None and self._reg_coeff is not None:
            regularization_coefficient = self._reg_coeff
        elif regularization_coefficient is None and self._reg_coeff is None:
            regularization_coefficient = 0.0

        if self._with_patching:
            patches_horiz = self._image_width // self._patch_width
            patches_vert = self._image_height // self._patch_height
            batch_multiplier = patches_horiz * patches_vert
        else:
            batch_multiplier = 1

        last_layer_dims = copy.deepcopy(self.__last_layer().output_size)
        with self._graph.as_default():
            layer = layers.upsampleLayer(layer_name,
                                         last_layer_dims,
                                         filter_size,
                                         num_filters,
                                         upscale_factor,
                                         activation_function,
                                         batch_multiplier,
                                         self._weight_initializer,
                                         regularization_coefficient)

        self.__log('Filter dimensions: {0} Outputs: {1}'.format(layer.weights_shape, layer.output_size))

        self._layers.append(layer)

    def add_pooling_layer(self, kernel_size, stride_length, pooling_type='max'):
        """
        Add a pooling layer to the model.

        :param kernel_size: an integer representing the width and height dimensions of the pooling operation
        :param stride_length: convolution stride length
        :param pooling_type: optional, the type of pooling operation
        """
        if len(self._layers) < 1:
            raise RuntimeError("A pooling layer cannot be the first layer added to the model. " +
                               "Add an input layer with DPPModel.add_input_layer() first.")
        if not isinstance(kernel_size, int):
            raise TypeError("kernel_size must be an int")
        if kernel_size <= 0:
            raise ValueError("kernel_size must be positive")
        if not isinstance(stride_length, int):
            raise TypeError("stride_length must be an int")
        if stride_length <= 0:
            raise ValueError("stride_length must be positive")
        if not isinstance(pooling_type, str):
            raise TypeError("pooling_type must be a str")
        pooling_type = pooling_type.lower()
        if pooling_type not in self._supported_pooling_types:
            raise ValueError("'" + pooling_type + "' is not one of the currently supported pooling types." +
                             " Choose one of: " +
                             " ".join("'"+x+"'" for x in self._supported_pooling_types))

        self._num_layers_pool += 1
        layer_name = 'pool%d' % self._num_layers_pool
        self.__log('Adding pooling layer %s...' % layer_name)

        with self._graph.as_default():
            layer = layers.poolingLayer(copy.deepcopy(
                self.__last_layer().output_size), kernel_size, stride_length, pooling_type)

        self.__log('Outputs: %s' % layer.output_size)

        self._layers.append(layer)

    def add_normalization_layer(self):
        """Add a local response normalization layer to the model"""
        if len(self._layers) < 1:
            raise RuntimeError("A normalization layer cannot be the first layer added to the model. " +
                               "Add an input layer with DPPModel.add_input_layer() first.")

        self._num_layers_norm += 1
        layer_name = 'norm%d' % self._num_layers_pool
        self.__log('Adding pooling layer %s...' % layer_name)

        with self._graph.as_default():
            layer = layers.normLayer(copy.deepcopy(self.__last_layer().output_size))

        self._layers.append(layer)

    def add_dropout_layer(self, p):
        """
        Add a DropOut layer to the model.

        :param p: the keep-probability parameter for the DropOut operation
        """
        if len(self._layers) < 1:
            raise RuntimeError("A dropout layer cannot be the first layer added to the model. " +
                               "Add an input layer with DPPModel.add_input_layer() first.")
        if not isinstance(p, float):
            raise TypeError("p must be a float")
        if p < 0 or p >= 1:
            raise ValueError("p must be in range [0, 1)")

        self._num_layers_dropout += 1
        layer_name = 'drop%d' % self._num_layers_dropout
        self.__log('Adding dropout layer %s...' % layer_name)

        with self._graph.as_default():
            layer = layers.dropoutLayer(copy.deepcopy(self.__last_layer().output_size), p)

        self._layers.append(layer)

    def add_batch_norm_layer(self):
        """Add a batch normalization layer to the model."""
        if len(self._layers) < 1:
            raise RuntimeError("A batch norm layer cannot be the first layer added to the model.")

        self._num_layers_batchnorm += 1
        layer_name = 'bn%d' % self._num_layers_batchnorm
        self.__log('Adding batch norm layer %s...' % layer_name)

        with self._graph.as_default():
            layer = layers.batchNormLayer(layer_name, copy.deepcopy(self.__last_layer().output_size))

        self._layers.append(layer)

    def add_fully_connected_layer(self, output_size, activation_function, regularization_coefficient=None):
        """
        Add a fully connected layer to the model.

        :param output_size: the number of units in the layer
        :param activation_function: optionally, the activation function to use
        :param regularization_coefficient: optionally, an L2 decay coefficient for this layer (overrides the coefficient
         set by set_regularization_coefficient)
        """
        if len(self._layers) < 1:
            raise RuntimeError("A fully connected layer cannot be the first layer added to the model. " +
                               "Add an input layer with DPPModel.add_input_layer() first.")
        if not isinstance(output_size, int):
            raise TypeError("output_size must be an int")
        if output_size <= 0:
            raise ValueError("output_size must be positive")
        if not isinstance(activation_function, str):
            raise TypeError("activation_function must be a str")
        activation_function = activation_function.lower()
        if activation_function not in self._supported_activation_functions:
            raise ValueError("'" + activation_function + "' is not one of the currently supported activation " +
                             "functions. Choose one of: " +
                             " ".join("'"+x+"'" for x in self._supported_activation_functions))
        if regularization_coefficient is not None:
            if not isinstance(regularization_coefficient, float):
                raise TypeError("regularization_coefficient must be a float or None")
            if regularization_coefficient < 0:
                raise ValueError("regularization_coefficient must be non-negative")

        self._num_layers_fc += 1
        layer_name = 'fc%d' % self._num_layers_fc
        self.__log('Adding fully connected layer %s...' % layer_name)

        reshape = self.__last_layer_outputs_volume()

        if regularization_coefficient is None and self._reg_coeff is not None:
            regularization_coefficient = self._reg_coeff
        if regularization_coefficient is None and self._reg_coeff is None:
            regularization_coefficient = 0.0

        with self._graph.as_default():
            layer = layers.fullyConnectedLayer(layer_name,
                                               copy.deepcopy(self.__last_layer().output_size),
                                               output_size,
                                               reshape,
                                               self._batch_size,
                                               activation_function,
                                               self._weight_initializer,
                                               regularization_coefficient)

        self.__log('Inputs: {0} Outputs: {1}'.format(layer.input_size, layer.output_size))

        self._layers.append(layer)

    @abstractmethod
    def add_output_layer(self, regularization_coefficient=None, output_size=None):
        """
        Add an output layer to the network (affine layer where the number of units equals the number of network outputs)

        :param regularization_coefficient: optionally, an L2 decay coefficient for this layer (overrides the coefficient
         set by set_regularization_coefficient)
        :param output_size: optionally, override the output size of this layer. Typically not needed, but required for
        use cases such as creating the output layer before loading data.
        """
        pass

    def use_predefined_model(self, model_name):
        """
        Add network layers to build a predefined network model
        :param model_name: The predefined model name
        """
        if model_name not in self._supported_predefined_models:
            raise ValueError("'" + model_name + "' is not one of the currently supported predefined models." +
                             " Make sure you have the correct problem type set with DPPModel.set_problem_type() " +
                             "first, or choose one of " +
                             " ".join("'" + x + "'" for x in self._supported_predefined_models))

        if model_name == 'vgg-16':
            self.add_input_layer()

            self.add_convolutional_layer(filter_dimension=[3, 3, self._image_depth, 64],
                                         stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 64, 64], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)

            self.add_convolutional_layer(filter_dimension=[3, 3, 64, 128], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 128, 128], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)

            self.add_convolutional_layer(filter_dimension=[3, 3, 128, 256], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 256, 256], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)

            self.add_convolutional_layer(filter_dimension=[3, 3, 256, 512], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 512], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 512], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)

            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 512], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 512], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 512], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)

            self.add_fully_connected_layer(output_size=4096, activation_function='relu')
            self.add_dropout_layer(0.5)
            self.add_fully_connected_layer(output_size=4096, activation_function='relu')
            self.add_dropout_layer(0.5)

            self.add_output_layer()

        if model_name == 'alexnet':
            self.add_input_layer()

            self.add_convolutional_layer(filter_dimension=[11, 11, self._image_depth, 48],
                                         stride_length=4, activation_function='relu')
            self.add_normalization_layer()
            self.add_pooling_layer(kernel_size=3, stride_length=2)

            self.add_convolutional_layer(filter_dimension=[5, 5, 48, 256], stride_length=1, activation_function='relu')
            self.add_normalization_layer()
            self.add_pooling_layer(kernel_size=3, stride_length=2)

            self.add_convolutional_layer(filter_dimension=[3, 3, 256, 384], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 384, 384], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 384, 256], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=3, stride_length=2)

            self.add_fully_connected_layer(output_size=4096, activation_function='relu')
            self.add_dropout_layer(0.5)
            self.add_fully_connected_layer(output_size=4096, activation_function='relu')
            self.add_dropout_layer(0.5)

            self.add_output_layer()

        if model_name == 'xsmall':
            self.add_input_layer()

            self.add_convolutional_layer(filter_dimension=[3, 3, self._image_depth, 16],
                                         stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)

            self.add_convolutional_layer(filter_dimension=[3, 3, 16, 32], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)

            self.add_convolutional_layer(filter_dimension=[3, 3, 32, 32], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)

            self.add_fully_connected_layer(output_size=64, activation_function='relu')

            self.add_output_layer()

        if model_name == 'small':
            self.add_input_layer()

            self.add_convolutional_layer(filter_dimension=[3, 3, self._image_depth, 64],
                                         stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)
            self.add_batch_norm_layer()

            self.add_convolutional_layer(filter_dimension=[3, 3, 64, 128], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 128, 128], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)
            self.add_batch_norm_layer()

            self.add_convolutional_layer(filter_dimension=[3, 3, 128, 128], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 128, 128], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)
            self.add_batch_norm_layer()

            self.add_fully_connected_layer(output_size=64, activation_function='relu')

            self.add_output_layer()

        if model_name == 'medium':
            self.add_input_layer()

            self.add_convolutional_layer(filter_dimension=[3, 3, self._image_depth, 64],
                                         stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 64, 64], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)
            self.add_batch_norm_layer()

            self.add_convolutional_layer(filter_dimension=[3, 3, 64, 128], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 128, 128], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)
            self.add_batch_norm_layer()

            self.add_convolutional_layer(filter_dimension=[3, 3, 128, 256], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 256, 256], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)
            self.add_batch_norm_layer()

            self.add_convolutional_layer(filter_dimension=[3, 3, 256, 512], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 512], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 512], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)
            self.add_batch_norm_layer()

            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 512], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 512], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 512], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)
            self.add_batch_norm_layer()

            self.add_fully_connected_layer(output_size=256, activation_function='relu')

            self.add_output_layer()

        if model_name == 'large':
            self.add_input_layer()

            self.add_convolutional_layer(filter_dimension=[3, 3, self._image_depth, 64],
                                         stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 64, 64], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)
            self.add_batch_norm_layer()

            self.add_convolutional_layer(filter_dimension=[3, 3, 64, 128], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 128, 128], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)
            self.add_batch_norm_layer()

            self.add_convolutional_layer(filter_dimension=[3, 3, 128, 256], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 256, 256], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)
            self.add_batch_norm_layer()

            self.add_convolutional_layer(filter_dimension=[3, 3, 256, 512], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 512], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 512], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)
            self.add_batch_norm_layer()

            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 512], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 512], stride_length=1, activation_function='relu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 512], stride_length=1, activation_function='relu')
            self.add_pooling_layer(kernel_size=2, stride_length=2)
            self.add_batch_norm_layer()

            self.add_fully_connected_layer(output_size=512, activation_function='relu')
            self.add_fully_connected_layer(output_size=384, activation_function='relu')

            self.add_output_layer()

        if model_name == 'yolov2':
            self.add_input_layer()

            self.add_convolutional_layer(filter_dimension=[3, 3, self._image_depth, 32],
                                         stride_length=1, activation_function='lrelu')
            self.add_pooling_layer(kernel_size=3, stride_length=2)

            self.add_convolutional_layer(filter_dimension=[3, 3, 32, 64], stride_length=1, activation_function='lrelu')
            self.add_pooling_layer(kernel_size=3, stride_length=2)

            self.add_convolutional_layer(filter_dimension=[3, 3, 64, 128], stride_length=1, activation_function='lrelu')
            self.add_convolutional_layer(filter_dimension=[1, 1, 128, 64], stride_length=1, activation_function='lrelu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 64, 128], stride_length=1, activation_function='lrelu')
            self.add_pooling_layer(kernel_size=3, stride_length=2)

            self.add_convolutional_layer(filter_dimension=[3, 3, 128, 256],
                                         stride_length=1, activation_function='lrelu')
            self.add_convolutional_layer(filter_dimension=[1, 1, 256, 128],
                                         stride_length=1, activation_function='lrelu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 128, 256],
                                         stride_length=1, activation_function='lrelu')
            self.add_pooling_layer(kernel_size=3, stride_length=2)

            self.add_convolutional_layer(filter_dimension=[3, 3, 256, 512],
                                         stride_length=1, activation_function='lrelu')
            self.add_convolutional_layer(filter_dimension=[1, 1, 512, 256],
                                         stride_length=1, activation_function='lrelu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 256, 512],
                                         stride_length=1, activation_function='lrelu')
            self.add_convolutional_layer(filter_dimension=[1, 1, 512, 256],
                                         stride_length=1, activation_function='lrelu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 256, 512],
                                         stride_length=1, activation_function='lrelu')
            self.add_pooling_layer(kernel_size=3, stride_length=2)

            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 1024],
                                         stride_length=1, activation_function='lrelu')
            self.add_convolutional_layer(filter_dimension=[1, 1, 1024, 512],
                                         stride_length=1, activation_function='lrelu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 1024],
                                         stride_length=1, activation_function='lrelu')
            self.add_convolutional_layer(filter_dimension=[1, 1, 1024, 512],
                                         stride_length=1, activation_function='lrelu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 512, 1024],
                                         stride_length=1, activation_function='lrelu')
            self.add_pooling_layer(kernel_size=3, stride_length=2)

            self.add_convolutional_layer(filter_dimension=[3, 3, 1024, 1024],
                                         stride_length=1, activation_function='lrelu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 1024, 1024],
                                         stride_length=1, activation_function='lrelu')
            self.add_convolutional_layer(filter_dimension=[3, 3, 1024, 1024],
                                         stride_length=1, activation_function='lrelu')

            self.add_output_layer()

    def load_dataset_from_directory_with_csv_labels(self, dirname, labels_file, column_number=False):
        """
        Loads the png images in the given directory into an internal representation, using the labels provided in a CSV
        file.

        :param dirname: the path of the directory containing the images
        :param labels_file: the path of the .csv file containing the labels
        :param column_number: the column number (zero-indexed) of the column in the csv file representing the label
        """
        if not isinstance(dirname, str):
            raise TypeError("dirname must be a str")
        if not os.path.isdir(dirname):
            raise ValueError("'"+dirname+"' does not exist")
        if not isinstance(labels_file, str):
            raise TypeError("labels_file must be a str")

        image_files = [os.path.join(dirname, name) for name in os.listdir(dirname) if
                       os.path.isfile(os.path.join(dirname, name)) & name.endswith('.png')]

        labels = loaders.read_csv_labels(labels_file, column_number)

        self._total_raw_samples = len(image_files)
        self._total_classes = len(set(labels))

        self.__log('Total raw examples is %d' % self._total_raw_samples)
        self.__log('Total classes is %d' % self._total_classes)

        self._raw_image_files = image_files
        self._raw_labels = labels
        self._split_labels = False  # Band-aid fix

    def load_ippn_tray_dataset_from_directory(self, dirname):
        """
        Loads the RGB tray images and plant bounding box labels from the International Plant Phenotyping Network
        dataset.
        """
        self._resize_bbox_coords = True

        images = [os.path.join(dirname, name) for name in sorted(os.listdir(dirname)) if
                  os.path.isfile(os.path.join(dirname, name)) & name.endswith('_rgb.png')]

        label_files = [os.path.join(dirname, name) for name in sorted(os.listdir(dirname)) if
                       os.path.isfile(os.path.join(dirname, name)) & name.endswith('_bbox.csv')]

        # currently reads columns, need to read rows instead!!!
        labels = [loaders.read_csv_rows(label_file) for label_file in label_files]

        self._all_labels = []
        for label in labels:
            curr_label = []
            for nums in label:
                curr_label.extend(loaders.box_coordinates_to_pascal_voc_coordinates(nums))
            self._all_labels.append(curr_label)

        self._total_raw_samples = len(images)

        self.__log('Total raw examples is %d' % self._total_raw_samples)
        self.__log('Parsing dataset...')

        self._raw_image_files = images
        self._raw_labels = self._all_labels

    def load_ippn_leaf_count_dataset_from_directory(self, dirname):
        """Loads the RGB images and species labels from the International Plant Phenotyping Network dataset."""
        if self._image_height is None or self._image_width is None or self._image_depth is None:
            raise RuntimeError("Image dimensions need to be set before loading data." +
                               " Try using DPPModel.set_image_dimensions() first.")
        if self._maximum_training_batches is None:
            raise RuntimeError("The number of maximum training epochs needs to be set before loading data." +
                               " Try using DPPModel.set_maximum_training_epochs() first.")

        labels, ids = loaders.read_csv_labels_and_ids(os.path.join(dirname, 'Leaf_counts.csv'), 1, 0)

        # labels must be lists
        labels = [[label] for label in labels]

        image_files = [os.path.join(dirname, im_id + '_rgb.png') for im_id in ids]

        self._total_raw_samples = len(image_files)

        self.__log('Total raw examples is %d' % self._total_raw_samples)
        self.__log('Parsing dataset...')

        self._raw_image_files = image_files
        self._raw_labels = labels

    def load_inra_dataset_from_directory(self, dirname):
        """Loads the RGB images and labels from the INRA dataset."""

        labels, ids = loaders.read_csv_labels_and_ids(os.path.join(dirname, 'AutomatonImages.csv'), 1, 3, character=';')

        # Remove the header line
        labels.pop(0)
        ids.pop(0)

        image_files = [os.path.join(dirname, im_id) for im_id in ids]

        self._total_raw_samples = len(image_files)
        self._total_classes = len(set(labels))

        # transform into numerical one-hot labels
        labels = loaders.string_labels_to_sequential(labels)
        labels = tf.one_hot(labels, self._total_classes)

        self.__log('Total raw examples is %d' % self._total_raw_samples)
        self.__log('Total classes is %d' % self._total_classes)
        self.__log('Parsing dataset...')

        self._raw_image_files = image_files
        self._raw_labels = labels

    def load_cifar10_dataset_from_directory(self, dirname):
        """
        Loads the images and labels from a directory containing the CIFAR-10 image classification dataset as
        downloaded by nvidia DIGITS.
        """

        train_dir = os.path.join(dirname, 'train')
        test_dir = os.path.join(dirname, 'test')
        self._total_classes = 10
        self._queue_capacity = 60000

        train_labels, train_images = loaders.read_csv_labels_and_ids(os.path.join(train_dir, 'train.txt'), 1, 0,
                                                                     character=' ')

        def one_hot(labels, num_classes):
            return [[1 if i == label else 0 for i in range(num_classes)] for label in labels]

        # transform into numerical one-hot labels
        train_labels = [int(label) for label in train_labels]
        train_labels = one_hot(train_labels, self._total_classes)

        test_labels, test_images = loaders.read_csv_labels_and_ids(os.path.join(test_dir, 'test.txt'), 1, 0,
                                                                   character=' ')

        # transform into numerical one-hot labels
        test_labels = [int(label) for label in test_labels]
        test_labels = one_hot(test_labels, self._total_classes)

        self._total_raw_samples = len(train_images) + len(test_images)
        self._test_split = len(test_images) / self._total_raw_samples

        self.__log('Total raw examples is %d' % self._total_raw_samples)
        self.__log('Total classes is %d' % self._total_classes)

        self._raw_test_image_files = test_images
        self._raw_train_image_files = train_images
        self._raw_test_labels = test_labels
        self._raw_train_labels = train_labels
        if not self._testing:
            self._raw_train_image_files.extend(self._raw_test_image_files)
            self._raw_test_image_files = []
            self._raw_train_labels.extend(self._raw_test_labels)
            self._raw_test_labels = []
            self._test_split = 0
        if self._validation:
            num_val_samples = int(self._total_raw_samples * self._validation_split)
            self._raw_val_image_files = self._raw_train_image_files[:num_val_samples]
            self._raw_train_image_files = self._raw_train_image_files[num_val_samples:]
            self._raw_val_labels = self._raw_train_labels[:num_val_samples]
            self._raw_train_labels = self._raw_train_labels[num_val_samples:]

    def load_dataset_from_directory_with_auto_labels(self, dirname):
        """Loads the png images in the given directory, using subdirectories to separate classes."""

        # Load all file names and labels into arrays
        subdirs = list(filter(lambda item: os.path.isdir(item) & (item != '.DS_Store'),
                              [os.path.join(dirname, f) for f in os.listdir(dirname)]))

        num_classes = len(subdirs)

        image_files = []
        labels = np.array([])

        for sd in subdirs:
            image_paths = [os.path.join(sd, name) for name in os.listdir(sd) if
                           os.path.isfile(os.path.join(sd, name)) & name.endswith('.png')]
            image_files = image_files + image_paths

            # for one-hot labels
            current_labels = np.zeros((num_classes, len(image_paths)))
            current_labels[self._total_classes, :] = 1
            labels = np.hstack([labels, current_labels]) if labels.size else current_labels
            self._total_classes += 1

        labels = tf.transpose(labels)

        self._total_raw_samples = len(image_files)

        self.__log('Total raw examples is %d' % self._total_raw_samples)
        self.__log('Total classes is %d' % self._total_classes)
        self.__log('Parsing dataset...')

        self._raw_image_files = image_files
        self._raw_labels = labels

    def load_lemnatec_images_from_directory(self, dirname):
        """
        Loads the RGB (VIS) images from a Lemnatec plant scanner image dataset. Unless you only want to do
        preprocessing, regression or classification labels MUST be loaded first.
        """

        # Load all snapshot subdirectories
        subdirs = list(filter(lambda item: os.path.isdir(item) & (item != '.DS_Store'),
                              [os.path.join(dirname, f) for f in os.listdir(dirname)]))

        image_files = []

        # Load the VIS images in each subdirectory
        for sd in subdirs:
            image_paths = [os.path.join(sd, name) for name in os.listdir(sd) if
                           os.path.isfile(os.path.join(sd, name)) & name.startswith('VIS_SV_')]

            image_files = image_files + image_paths

        # Put the image files in the order of the IDs (if there are any labels loaded)
        sorted_paths = []

        if self._all_labels is not None:
            for image_id in self._all_ids:
                path = list(filter(lambda item: item.endswith(image_id), [p for p in image_files]))
                assert len(path) == 1, 'Found no image or multiple images for %r' % image_id
                sorted_paths.append(path[0])
        else:
            sorted_paths = image_files

        self._total_raw_samples = len(sorted_paths)

        self.__log('Total raw examples is %d' % self._total_raw_samples)
        self.__log('Parsing dataset...')

        images = sorted_paths

        # prepare images for training (if there are any labels loaded)

        if self._all_labels is not None:
            labels = self._all_labels

            self._raw_image_files = images
            self._raw_labels = labels

    def load_images_from_list(self, image_files):
        """
        Loads images from a list of file names (strings). Regression or classification labels MUST be loaded first.
        """

        self._total_raw_samples = len(image_files)

        self.__log('Total raw examples is %d' % self._total_raw_samples)
        self.__log('Parsing dataset...')

        images = image_files

        self._raw_image_files = images
        if self._all_labels is not None:
            self._raw_labels = self._all_labels
        else:
            self._images_only = True

    def load_multiple_labels_from_csv(self, filepath, id_column=0):
        """
        Load multiple labels from a CSV file, for instance values for regression.
        Parameter id_column is the column number specifying the image file name.
        """

        self._all_labels, self._all_ids = loaders.read_csv_multi_labels_and_ids(filepath, id_column)

    def load_images_with_ids_from_directory(self, im_dir):
        """Loads images from a directory, relating them to labels by the IDs which were loaded from a CSV file"""

        # Load all images in directory
        image_files = [os.path.join(im_dir, name) for name in os.listdir(im_dir) if
                       os.path.isfile(os.path.join(im_dir, name)) & name.endswith('.png')]

        # Put the image files in the order of the IDs (if there are any labels loaded)
        sorted_paths = []

        if self._all_labels is not None:
            for image_id in self._all_ids:
                path = list(filter(lambda item: item.endswith('/' + image_id), [p for p in image_files]))
                assert len(path) == 1, 'Found no image or multiple images for %r' % image_id
                sorted_paths.append(path[0])
        else:
            sorted_paths = image_files

        self._total_raw_samples = len(sorted_paths)

        self.__log('Total raw examples is %d' % self._total_raw_samples)
        self.__log('Parsing dataset...')

        processed_images = sorted_paths

        # prepare images for training (if there are any labels loaded)
        if self._all_labels is not None:
            self._raw_image_files = processed_images
            self._raw_labels = self._all_labels

    def load_training_augmentation_dataset_from_directory_with_csv_labels(self, dirname, labels_file, column_number=1,
                                                                          id_column_number=0):
        """
        Loads the png images from a directory as training augmentation images, using the labels provided in a CSV file.

        :param dirname: the path of the directory containing the images
        :param labels_file: the path of the .csv file containing the labels
        :param column_number: the column number (zero-indexed) of the column in the csv file representing the label
        :param id_column_number: the column number (zero-indexed) representing the file ID
        """

        image_files = [os.path.join(dirname, name) for name in os.listdir(dirname) if
                       os.path.isfile(os.path.join(dirname, name)) & name.endswith('.png')]

        labels, ids = loaders.read_csv_labels_and_ids(labels_file, column_number, id_column_number)

        sorted_paths = []

        for image_id in ids:
            path = list(filter(lambda item: item.endswith('/' + image_id), [p for p in image_files]))
            assert len(path) == 1, 'Found no image or multiple images for %r' % image_id
            sorted_paths.append(path[0])

        self._training_augmentation_images = sorted_paths
        self._training_augmentation_labels = labels

    def load_pascal_voc_labels_from_directory(self, data_dir):
        """Loads single per-image bounding boxes from XML files in Pascal VOC format."""

        self._all_ids = []
        self._all_labels = []

        file_paths = [os.path.join(data_dir, name) for name in os.listdir(data_dir) if
                      os.path.isfile(os.path.join(data_dir, name)) & name.endswith('.xml')]

        for voc_file in file_paths:
            im_id, x_min, x_max, y_min, y_max = loaders.read_single_bounding_box_from_pascal_voc(voc_file)

            # re-scale coordinates if images are being resized
            if self._resize_images:
                x_min = int(x_min * (float(self._image_width) / self._image_width_original))
                x_max = int(x_max * (float(self._image_width) / self._image_width_original))
                y_min = int(y_min * (float(self._image_height) / self._image_height_original))
                y_max = int(y_max * (float(self._image_height) / self._image_height_original))

            self._all_ids.append(im_id)
            self._all_labels.append([x_min, x_max, y_min, y_max])

    def load_json_labels_from_file(self, filename):
        """Loads bounding boxes for multiple images from a single json file."""

        self._all_ids = []
        self._all_labels = []

        with open(filename, 'r') as f:
            box_data = json.load(f)
        for box in sorted(box_data.items()):
            self._all_ids.append(box[0])  # Name of corresponding image
            w_original = box[1]['width']
            h_original = box[1]['height']
            boxes = []
            for plant in box[1]['plants']:
                x_min = plant['all_points_x'][0]
                x_max = plant['all_points_x'][1]
                y_min = plant['all_points_y'][0]
                y_max = plant['all_points_y'][1]

                # re-scale coordinates if images are being resized
                if self._resize_images:
                    x_min = int(x_min * (float(self._image_width) / w_original))
                    x_max = int(x_max * (float(self._image_width) / w_original))
                    y_min = int(y_min * (float(self._image_height) / h_original))
                    y_max = int(y_max * (float(self._image_height) / h_original))

                boxes.append([x_min, x_max, y_min, y_max])
            self._all_labels.append(boxes)

    def __parse_dataset(self, train_images, train_labels, train_mf,
                        test_images, test_labels, test_mf,
                        val_images, val_labels, val_mf,
                        image_type='png'):
        """Takes training and testing images and labels, creates input queues internally to this instance"""
        with self._graph.as_default():

            # Try to get the number of samples the normal way
            if isinstance(train_images, tf.Tensor):
                self._total_training_samples = train_images.get_shape().as_list()[0]
                if self._testing:
                    self._total_testing_samples = test_images.get_shape().as_list()[0]
                if self._validation:
                    self._total_validation_samples = val_images.get_shape().as_list()[0]
            elif isinstance(train_images[0], tf.Tensor):
                self._total_training_samples = train_images[0].get_shape().as_list()[0]
            else:
                self._total_training_samples = len(train_images)
                if self._testing:
                    self._total_testing_samples = len(test_images)
                if self._validation:
                    self._total_validation_samples = len(val_images)

            # Most often train/test/val_images will be a tensor with shape (?,), from tf.dynamic_partition, which
            # will have None for its size, so the above won't work and we manually calculate it here
            if self._total_training_samples is None:
                self._total_training_samples = int(self._total_raw_samples)
                if self._testing:
                    self._total_testing_samples = int(self._total_raw_samples * self._test_split)
                    self._total_training_samples = self._total_training_samples - self._total_testing_samples
                if self._validation:
                    self._total_validation_samples = int(self._total_raw_samples * self._validation_split)
                    self._total_training_samples = self._total_training_samples - self._total_validation_samples

            # Logging verbosity
            self.__log('Total training samples is {0}'.format(self._total_training_samples))
            self.__log('Total validation samples is {0}'.format(self._total_validation_samples))
            self.__log('Total testing samples is {0}'.format(self._total_testing_samples))

            # Create moderation features queues
            if train_mf is not None:
                train_moderation_queue = tf.train.slice_input_producer([train_mf], shuffle=False)
                self._train_moderation_features = tf.cast(train_moderation_queue[0], tf.float32)

            if test_mf is not None:
                test_moderation_queue = tf.train.slice_input_producer([test_mf], shuffle=False)
                self._test_moderation_features = tf.cast(test_moderation_queue[0], tf.float32)

            if val_mf is not None:
                val_moderation_queue = tf.train.slice_input_producer([val_mf], shuffle=False)
                self._val_moderation_features = tf.cast(val_moderation_queue[0], tf.float32)

            # Calculate number of batches to run
            batches_per_epoch = self._total_training_samples / float(self._batch_size)
            self._maximum_training_batches = int(self._maximum_training_batches * batches_per_epoch)

            if self._batch_size > self._total_training_samples:
                self.__log('Less than one batch in training set, exiting now')
                exit()
            self.__log('Batches per epoch: {:f}'.format(batches_per_epoch))
            self.__log('Running to {0} batches'.format(self._maximum_training_batches))

            # Create input queues
            train_input_queue = tf.train.slice_input_producer([train_images, train_labels], shuffle=False)
            if self._testing:
                test_input_queue = tf.train.slice_input_producer([test_images, test_labels], shuffle=False)
            if self._validation:
                val_input_queue = tf.train.slice_input_producer([val_images, val_labels], shuffle=False)

            self._train_labels = train_input_queue[1]
            if self._testing:
                self._test_labels = test_input_queue[1]
            if self._validation:
                self._val_labels = val_input_queue[1]

            # Apply pre-processing for training and testing images
            if image_type is 'jpg':
                self._train_images = tf.image.decode_jpeg(tf.read_file(train_input_queue[0]),
                                                          channels=self._image_depth)
                if self._testing:
                    self._test_images = tf.image.decode_jpeg(tf.read_file(test_input_queue[0]),
                                                             channels=self._image_depth)
                if self._validation:
                    self._val_images = tf.image.decode_jpeg(tf.read_file(val_input_queue[0]),
                                                            channels=self._image_depth)
            else:
                self._train_images = tf.image.decode_png(tf.read_file(train_input_queue[0]),
                                                         channels=self._image_depth)
                if self._testing:
                    self._test_images = tf.image.decode_png(tf.read_file(test_input_queue[0]),
                                                            channels=self._image_depth)
                if self._validation:
                    self._val_images = tf.image.decode_png(tf.read_file(val_input_queue[0]),
                                                           channels=self._image_depth)

            # Convert images to float and normalize to 1.0
            self._train_images = tf.image.convert_image_dtype(self._train_images, dtype=tf.float32)
            if self._testing:
                self._test_images = tf.image.convert_image_dtype(self._test_images, dtype=tf.float32)
            if self._validation:
                self._val_images = tf.image.convert_image_dtype(self._val_images, dtype=tf.float32)

            if self._resize_images:
                self._train_images = tf.image.resize_images(self._train_images,
                                                            [self._image_height, self._image_width])
                if self._testing:
                    self._test_images = tf.image.resize_images(self._test_images,
                                                               [self._image_height, self._image_width])
                if self._validation:
                    self._val_images = tf.image.resize_images(self._val_images,
                                                              [self._image_height, self._image_width])

            # Apply the various augmentations to the images
            if self._augmentation_crop:
                # Apply random crops to images
                self._image_height = int(self._image_height * self._crop_amount)
                self._image_width = int(self._image_width * self._crop_amount)

                self._train_images = tf.random_crop(self._train_images, [self._image_height, self._image_width, 3])
                if self._testing:
                    self._test_images = tf.image.resize_image_with_crop_or_pad(self._test_images, self._image_height,
                                                                               self._image_width)
                if self._validation:
                    self._val_images = tf.image.resize_image_with_crop_or_pad(self._val_images, self._image_height,
                                                                              self._image_width)

            if self._crop_or_pad_images:
                # Apply padding or cropping to deal with images of different sizes
                self._train_images = tf.image.resize_image_with_crop_or_pad(self._train_images,
                                                                            self._image_height,
                                                                            self._image_width)
                if self._testing:
                    self._test_images = tf.image.resize_image_with_crop_or_pad(self._test_images,
                                                                               self._image_height,
                                                                               self._image_width)
                if self._validation:
                    self._val_images = tf.image.resize_image_with_crop_or_pad(self._val_images,
                                                                              self._image_height,
                                                                              self._image_width)

            if self._augmentation_flip_horizontal:
                # Apply random horizontal flips
                self._train_images = tf.image.random_flip_left_right(self._train_images)

            if self._augmentation_flip_vertical:
                # Apply random vertical flips
                self._train_images = tf.image.random_flip_up_down(self._train_images)

            if self._augmentation_contrast:
                # Apply random contrast and brightness adjustments
                self._train_images = tf.image.random_brightness(self._train_images, max_delta=63)
                self._train_images = tf.image.random_contrast(self._train_images, lower=0.2, upper=1.8)

            if self._augmentation_rotate:
                # Apply random rotations, then optionally crop out black borders and resize
                angle = tf.random_uniform([], maxval=2*math.pi)
                self._train_images = tf.contrib.image.rotate(self._train_images, angle, interpolation='BILINEAR')
                if self._rotate_crop_borders:
                    # Cropping is done using the smallest fraction possible for the image's aspect ratio to maintain a
                    # consistent scale across the images
                    small_crop_fraction = self.__smallest_crop_fraction()
                    self._train_images = tf.image.central_crop(self._train_images, small_crop_fraction)
                    self._train_images = tf.image.resize_images(self._train_images,
                                                                [self._image_height, self._image_width])

            # mean-center all inputs
            self._train_images = tf.image.per_image_standardization(self._train_images)
            if self._testing:
                self._test_images = tf.image.per_image_standardization(self._test_images)
            if self._validation:
                self._val_images = tf.image.per_image_standardization(self._val_images)

            # define the shape of the image tensors so it matches the shape of the images
            self._train_images.set_shape([self._image_height, self._image_width, self._image_depth])
            if self._testing:
                self._test_images.set_shape([self._image_height, self._image_width, self._image_depth])
            if self._validation:
                self._val_images.set_shape([self._image_height, self._image_width, self._image_depth])

    def __parse_images(self, images, image_type='png'):
        """Takes some images as input, creates producer of processed images internally to this instance"""
        with self._graph.as_default():
            input_queue = tf.train.string_input_producer(images, shuffle=False)

            reader = tf.WholeFileReader()
            key, file = reader.read(input_queue)

            # pre-processing for all images

            if image_type is 'jpg':
                input_images = tf.image.decode_jpeg(file, channels=self._image_depth)
            else:
                input_images = tf.image.decode_png(file, channels=self._image_depth)

            # convert images to float and normalize to 1.0
            input_images = tf.image.convert_image_dtype(input_images, dtype=tf.float32)

            if self._resize_images is True:
                input_images = tf.image.resize_images(input_images, [self._image_height, self._image_width])

            if self._augmentation_crop is True:
                self._image_height = int(self._image_height * self._crop_amount)
                self._image_width = int(self._image_width * self._crop_amount)
                input_images = tf.image.resize_image_with_crop_or_pad(input_images, self._image_height,
                                                                      self._image_width)

            if self._crop_or_pad_images is True:
                # pad or crop to deal with images of different sizes
                input_images = tf.image.resize_image_with_crop_or_pad(input_images, self._image_height,
                                                                      self._image_width)

            # mean-center all inputs
            input_images = tf.image.per_image_standardization(input_images)

            # define the shape of the image tensors so it matches the shape of the images
            input_images.set_shape([self._image_height, self._image_width, self._image_depth])

            self._all_images = input_images

    def __smallest_crop_fraction(self):
        """
        Determine the angle and crop fraction for rotated images that gives the maximum border-less crop area for a
        given angle but the smallest such area among all angles from 0-90 degrees. This is used during rotation
        augmentation to apply a consistent crop and maintain similar scale across all images. Using larger crop
        fractions based on the rotation angle would result in different scales.
        :return: The crop fraction that achieves the smallest area among border-less crops for rotated images
        """

        # Regardless of the aspect ratio, the smallest crop fraction always corresponds to the required crop for a 45
        # degree or pi/4 radian rotation
        angle = math.pi/4

        # Determine which sides of the original image are the shorter and longer sides
        width_is_longer = self._image_width >= self._image_height
        if width_is_longer:
            (short_length, long_length) = (self._image_height, self._image_width)
        else:
            (short_length, long_length) = (self._image_width, self._image_height)

        # Get the absolute sin and cos of the angle, since the quadrant doesn't affect us
        sin_a = abs(math.sin(angle))
        cos_a = abs(math.cos(angle))

        # There are 2 possible solutions for the width and height in general depending on the angle and aspect ratio,
        # but 45 degree rotations always fall into the solution below. This corresponds to a rectangle with one corner
        # at the midpoint of an edge and the other corner along the centre line of the rotated image, although this
        # cropped rectangle will ultimately be slid up so that it's centered inside the rotated image.
        x = 0.5 * short_length
        if width_is_longer:
            (crop_width, crop_height) = (x / sin_a, x / cos_a)
        else:
            (crop_width, crop_height) = (x / cos_a, x / sin_a)

        # Use the crop width and height to calculate the required crop ratio
        return (crop_width * crop_height) / (self._image_width * self._image_height)
