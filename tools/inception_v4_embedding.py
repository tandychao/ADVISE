from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import cv2
import numpy as np
import tensorflow as tf

from google.protobuf import text_format
from protos import feature_extractors_pb2

from feature_extractors import builder
from utils import vis
from utils.ads_api import AdsApi

flags = tf.app.flags
flags.DEFINE_string('feature_extractor', 'inception_v4_extractor', 'Name of the extractor.')
flags.DEFINE_string('checkpoint_path', '', 'Path to the pre-trained ckpt file.')

flags.DEFINE_string('ads_config', 'configs/ads_api_config', 'Directory to the ads config file.')

flags.DEFINE_float('score_threshold', 0.5, 'Threshold for filtering out low confident scores.')
flags.DEFINE_string('output_path', '', 'Path to the output file.')

FLAGS = flags.FLAGS
slim = tf.contrib.slim


config_str = "%s: {}" % (FLAGS.feature_extractor)


def _default_session_config_proto():
  """Get the default config proto for tensorflow session.

  Returns:
    config: The default config proto for tf.Session.
  """
  config = tf.ConfigProto()
  config.allow_soft_placement = True
  config.gpu_options.allow_growth = True
  config.gpu_options.per_process_gpu_memory_fraction = 1.0
  return config


def _visualize(batch_data):
  for i, image in enumerate(batch_data):
    image = vis.image_float32_to_uint8(image)
    vis.image_save('%s.jpg' % (i), image, convert_to_bgr=True)


def main(_):
  tf.logging.set_verbosity(tf.logging.INFO)

  # Create feature extractor.
  config = feature_extractors_pb2.FeatureExtractor()
  text_format.Merge(config_str, config)

  feature_extractor = builder.build(config)
  image_size = feature_extractor.default_image_size

  tf.logging.info('Default image size of %s is %s:', 
      type(feature_extractor).__name__, image_size)

  # Create computational graph.
  g = tf.Graph()
  with g.as_default():
    # Create model.
    inputs = tf.placeholder(
        shape=(None, image_size, image_size, 3),
        dtype=tf.float32)

    features = feature_extractor.extract_feature(inputs, is_training=False)
    init_fn = feature_extractor.assign_from_checkpoint_fn(FLAGS.checkpoint_path)
    invalid_tensor_names = tf.report_uninitialized_variables()

  # Start session.
  output = {}
  with tf.Session(graph=g, config=_default_session_config_proto()) as sess:
    init_fn(sess)

    invalid_tensor_names = sess.run(invalid_tensor_names)
    if len(invalid_tensor_names) > 0:
      raise ValueError('There are uninitialized variables!')

    api = AdsApi()
    api.init(images_dir=FLAGS.images_dir,
        entity_annot_file_ex=FLAGS.entity_annot_file)
    meta_list = api.get_meta_list_with_entity_annots_ex(
        score_threshold=FLAGS.score_threshold)
    tf.logging.info('Loaded %s examples.', len(meta_list))

    for meta_index, meta in enumerate(meta_list):
      image = vis.image_load(meta['filename'], convert_to_rgb=True)
      image = vis.image_uint8_to_float32(image)

      if meta_index % 10 == 0:
        tf.logging.info('On image %d of %d', meta_index, len(meta_list))

      # Batch operation, the first image is the full image.
      roi_list = [cv2.resize(image, (image_size, image_size))]
      score_list = []
      for entity in meta['entities_ex']:
        roi = vis.image_crop_and_resize(image, 
            box=(entity['xmin'], entity['ymin'], entity['xmax'], entity['ymax']), 
            crop_size=(image_size, image_size))
        roi_list.append(roi)
        score_list.append(entity['score'])
#      assert len(roi_list) == 11
#      assert len(score_list) == 10

      roi_batch = np.stack(roi_list, axis=0)
      if meta_index == 0:
        _visualize(roi_batch)

      # Get feature from inception model.
      embeddings = sess.run(pre_logits_flatten, feed_dict={inputs: roi_batch})
      output[meta['image_id']] = {
        'image_emb': embeddings[0],
        'entity_score_list': np.array(score_list),
        'entity_emb_list': embeddings[1:]
      }

  with open(FLAGS.output_path, 'wb') as fp:
    np.save(fp, output)

if __name__ == '__main__':
  tf.app.run()
