## Predefined Model Architectures
[Individual layers](Neural-Network-Layers) can be used to create any custom model. There are some common architectures, however, that don't have to be made from scratch and are predefined.

```python
model.use_predefined_model(model_name)
```

`model_name` defines the name of the predefined network. Currently supported networks include:

### `alexnet`

A classic image classification network (Krizhevsky, A., Sutskever, I., & Hinton, G. E. (2012). *Imagenet classification with deep convolutional neural networks.* In Advances in neural information processing systems (pp. 1097-1105).)

### `vgg-16`

A classic image classification network (Simonyan, K., & Zisserman, A. (2014). *Very deep convolutional networks for large-scale image recognition.* arXiv preprint arXiv:1409.1556.)


### `yolov2`

The convolutional architecture used by the authors of YOLOv2, the object detection system implemented in DPP. (Redmon, J., & Farhadi, A. (2017). *YOLO9000: better, faster, stronger.* In Proceedings of the IEEE conference on computer vision and pattern recognition (pp. 7263-7271).)

### `xsmall`

A tiny convolutional network with three low-capacity convolutional layers, three pooling layers, and a single small fully connected layer with 64 units. For simple problems which require a small memory footprint.

### `small`

A slightly higher-capacity feature extractor with five layers, using batch norm between each block of convolutional layers. Same 64-unit fully connected layer as `xsmall` to avoid overfitting problems with plant phenotyping datasets.

### `medium`

Uses the full vgg-16 feature extractor, but with batch normalization instead of dropout. A slightly larger 256-unit fully connected layer.

### `large`

Uses the full vgg-16 feature extractor, but with batch normalization instead of dropout. Two fully connected layers with 512 and 384 units respectively. 