import importlib
import argparse
from config import config
import os 
import pdb
import torch
import torch.nn as nn
import torch.utils.data
import torch.optim as optim
import tensorflow as tf
import numpy as np
from sklearn.metrics import roc_auc_score
from datetime import datetime
from tqdm import tqdm
import pickle




# Load multi task class
mtl_module = importlib.import_module(config.mtl_model)
mtl_class = getattr(mtl_module, config.mtl_model.upper())

# Load task-related code
task_module = importlib.import_module("task_codes." + config.task_code)
# Load task data and convert to pytorch
train_x_numpy , train_y_numpy, valid_x_numpy , valid_y_numpy, test_x_numpy , test_y_numpy, num_features, num_steps, num_tasks\
                 = task_module.load_data(tasks=config.tasks)

# config.KL_scale = len(train_x_numpy)

# config.KL_scale = len(train_x_numpy)

train_x = torch.from_numpy(train_x_numpy).type(torch.FloatTensor) 
train_y = torch.from_numpy(train_y_numpy).type(torch.FloatTensor)
valid_x = torch.from_numpy(valid_x_numpy).type(torch.FloatTensor)
valid_y = torch.from_numpy(valid_y_numpy).type(torch.FloatTensor) 
test_x = torch.from_numpy(test_x_numpy).type(torch.FloatTensor) 
test_y = torch.from_numpy(test_y_numpy).type(torch.FloatTensor)

# config.TOTAL_EPOCH = int(100000/(len(train_x)/config.BATCH_SIZE))

def make_dataloader():
    

    # modify task config accordingly
    config.num_features = num_features
    config.num_steps = num_steps
    config.num_tasks = num_tasks


    datasets = {}
    datasets["train"] = torch.utils.data.TensorDataset(train_x,train_y) 
    datasets["valid"] = torch.utils.data.TensorDataset(valid_x,valid_y)
    datasets["test"] = torch.utils.data.TensorDataset(test_x,test_y) 


    dataloader = {}
    dataloader["train"] = torch.utils.data.DataLoader(datasets["train"], batch_size=config.BATCH_SIZE, shuffle=True)
    dataloader["valid"] = torch.utils.data.DataLoader(datasets["valid"], batch_size=config.BATCH_SIZE, shuffle=False) 
    dataloader["test"] = torch.utils.data.DataLoader(datasets["test"], batch_size=config.BATCH_SIZE, shuffle=False)
    return dataloader



dataloader = make_dataloader()

net = mtl_class(config)



#GPU Option
gpu_options = tf.GPUOptions(allow_growth=True)
# gpu_usage = 0.96
# gpu_options = tf.GPUOptions(per_process_gpu_memory_fraction=gpu_usage)
sess = tf.InteractiveSession(config=tf.ConfigProto(gpu_options=gpu_options))

# run the model
sess.run(tf.global_variables_initializer())

save_path = "saved/%s_%s/"%(config.mtl_model,config.task_code)
if not os.path.isdir(save_path):
    os.makedirs(save_path)
saver = tf.train.Saver()
#saver.restore(sess, SAVE_DIR+"retain_mimic1400.ckpt")

def train_epoch():
    print("Training Model %s for task %s, learning rate decay: %.5f"%\
                        (config.mtl_model,config.task_code,sess.run(net.lr_decay)))
    total_loss_sum = 0
    total_loss_all = {}
    for batch_data, batch_labels in tqdm(dataloader["train"],ncols=75):
        feed_dict = {net.x:batch_data, net.y:batch_labels,net.num_samples_ph:1,net.train:True}
        _, loss_sum, loss_all = sess.run([net.optim,net.loss_sum,net.loss_all], feed_dict=feed_dict)
        total_loss_sum += loss_sum
        for s in loss_all:
            if s in total_loss_all:
                total_loss_all[s] += loss_all[s]
            else:
                total_loss_all[s] = loss_all[s]

    total_loss_sum = total_loss_sum/len(dataloader["train"])
    for s in total_loss_all:
        total_loss_all[s] = total_loss_all[s]/len(dataloader["train"])

    # feed_dict = {net.x[i]:train_x[i] for i in range(config.num_tasks)}
    # feed_dict.update({net.y[i]:train_y[i] for i in range(config.num_tasks)})
    # loss_sum, loss_all = sess.run([net.loss_sum, net.loss_all], feed_dict=feed_dict)
    print ('loss_sum', total_loss_sum)
    print ([s + ': %.3f'%(total_loss_all[s]) + '    ' for s in loss_all])


def valid_epoch():
    print("--------------------------------------------------------")
    print("Performance on valid set")
    total_loss = 0
    total_auc = []
    preds_each = {}
    loss_each = {}
    for s in range(config.num_samples):
        preds_each[s],loss_each[s] = sess.run([net.preds_each,net.loss_each], feed_dict={net.x:valid_x,net.y:valid_y})
    
    for task_id in range(config.num_tasks):
        preds_s = 0
        loss_s = 0
        for s in range(config.num_samples):
            preds, loss = preds_each[s][task_id], loss_each[s][task_id]
            preds_s += preds 
            loss_s += loss
        preds = preds_s / config.num_samples
        loss = loss_s / config.num_samples
        auc = roc_auc_score(valid_y_numpy[:,task_id:task_id+1],preds)
        total_loss += loss 
        total_auc.append(auc)
        print ("Task:",task_id,"   Loss:",loss,"   AUC:",auc)
    print("Total loss:",total_loss)
    return total_loss, total_auc


def train(e=0):
    # start training
    eval_loss_min = float('inf')
    eval_total_auc_best = [0 for _ in range(num_tasks)]
    eval_auc_best_for_each = [0 for _ in range(num_tasks)]
    epoch_min = 0
    best_model_filename = None

    try:
        for epoch in range(e,config.TOTAL_EPOCH):
            print("==========================================================")
            print(datetime.now(), best_model_filename)
            print ("Epoch: ",epoch+1)
            train_epoch()
            eval_loss, eval_auc = valid_epoch()
            if eval_loss<eval_loss_min:
                eval_loss_min = eval_loss
                eval_total_auc_best = eval_auc 
                epoch_min = epoch+1

                best_model_filename = save_path+'%d_%.3f.ckpt'%(epoch+1,eval_loss)
                saver.save(sess, best_model_filename)

            for task_id in range(config.num_tasks):
                if eval_auc_best_for_each[task_id] < eval_auc[task_id]:
                    eval_auc_best_for_each[task_id] = eval_auc[task_id]


    except KeyboardInterrupt:
        print()
    finally:
        print("******RESULT******")
        print("Valid loss min: %f at epoch %d. AUC for each task is:"%(eval_loss_min,epoch_min))
        for task_id in range(num_tasks):
            print(" (+) Task %d, AUC: %.5f"%(task_id,eval_total_auc_best[task_id]))
        print("Best AUC for each task is:")
        for task_id in range(num_tasks):
            print(" (+) Task %d, AUC: %.5f"%(task_id,eval_auc_best_for_each[task_id]))

    return best_model_filename

def inference():
    print("==========================================================")
    print("==========================================================")
    print("==========================================================")
    print("Performance of the optimal model on test set")
    total_loss = 0
    total_auc = []
    preds_each = {}
    loss_each = {}
    for s in range(config.num_samples):
        preds_each[s],loss_each[s] = sess.run([net.preds_each,net.loss_each], feed_dict={net.x:test_x,net.y:test_y})
    
    for task_id in range(config.num_tasks):
        preds_s = 0
        loss_s = 0
        for s in range(config.num_samples):
            preds, loss = preds_each[s][task_id], loss_each[s][task_id]
            preds_s += preds 
            loss_s += loss
        preds = preds_s / config.num_samples
        loss = loss_s / config.num_samples


        auc = roc_auc_score(test_y_numpy[:,task_id:task_id+1],preds)
        total_loss += loss 
        total_auc.append(auc)
        print ("Task:",task_id,"   Loss:",loss,"   AUC:",auc)
    print("Total loss:",total_loss)
    return total_loss, total_auc




if __name__=="__main__":
    starting_epoch = 0
    saved_model = 'saved/tp_amtl_physionet2012/824_1.095.ckpt'
    saver.restore(sess, saved_model)
    
    beta_outputs = []
    att_eachs = []
    for s in range(30):
        beta_output,att_each = sess.run([net.beta_output,net.att_each],feed_dict={net.x:valid_x,net.y:valid_y})
        beta_outputs.append(beta_output)
        att_eachs.append(att_each)
    import pickle
    pdb.set_trace()
    pickle.dump(beta_outputs,open('images_physionet/beta_outputs.pkl','wb'))
    pickle.dump(att_eachs,open('images_physionet/att_eachs.pkl','wb'))

    #saved_model = train(0)
    #saver.restore(sess, saved_model)
    valid_epoch()
    inference()
