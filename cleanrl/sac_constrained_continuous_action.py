# docs and experiment results can be found at https://docs.cleanrl.dev/rl-algorithms/sac/#sac_continuous_actionpy
import argparse
import os
import random
import time
from distutils.util import strtobool

import gym
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
# from stable_baselines3.common.buffers import ReplayBuffer
from sac_constrained_buffer import ReplayBuffer, CustomRecordEpisodeStatistics
from torch.utils.tensorboard import SummaryWriter
from gym.envs.registration import register
from polamp_env.lib.utils_operations import generateDataSet
import json
import os
import copy

print(f"we are here {os. getcwd()}")

dir = "polamp_env/"
with open(dir + "configs/environment_configs.json", 'r') as f:
    our_env_config = json.load(f)

with open(dir + "configs/reward_weight_configs.json", 'r') as f:
    reward_config = json.load(f)

with open(dir + "configs/car_configs.json", 'r') as f:
    car_config = json.load(f)

dataSet = generateDataSet(our_env_config, name_folder= dir + "maps", total_maps=12, dynamic=False)

def validate(env, actor, max_steps, save_image=False, id=None, val_key=None):
    if id is None or val_key is None:
        return
    actor.eval()
    state = env.reset(id=id, val_key=val_key)
    if save_image:
        images = []
        images.append(env.render())
    isDone = False
    t = 0
    sum_reward = 0
    episode_constrained = []
    episode_min_beam = []
    obs = torch.zeros((1, env.observation_space.shape[0]), dtype=torch.float32).to(device)
    while not isDone and t < max_steps:
        obs[0] = torch.Tensor(state).to(device)
        _, _, mean = actor.get_action(obs)
        action = mean.detach().cpu().numpy()[0]
        state, reward, isDone, info = env.step(action)
        sum_reward += reward
        episode_constrained.append(info.get('cost', 0))
        episode_min_beam.append(env.environment.min_beam)
        if save_image:
            images.append(env.render())
        t += 1
        
    env.close()
    if save_image:
        images = np.transpose(np.array(images), axes=[0, 3, 1, 2])
    return sum_reward if not save_image else images, isDone, info, np.mean(episode_constrained), np.min(episode_min_beam) 

def validation(env, agent):
    print("### Validation ###")
    actor = copy.deepcopy(agent)
    actor.eval()
    # actor.load_state_dict(torch.load(f'runs/{run_name}/actor.pkl'))
    collision_tasks = 0
    successed_tasks = 0
    total_tasks = 0
    constrained_cost = []
    lst_min_beam = []
    results_dictionary = {}
    print(env)
    for val_key in env.valTasks:
        eval_tasks = len(env.valTasks[val_key])
        total_tasks += eval_tasks
        print(f"val_key: {val_key}")
        print(f"eval_tasks: {eval_tasks}")
        for id in range(eval_tasks):
            images, isDone, info, episode_cost, min_beam = validate(env, actor, 250, id=id, val_key=val_key)
            constrained_cost.append(episode_cost)
            lst_min_beam.append(min_beam)
            if isDone:
                if "Collision" in info:
                    # collision = True
                    # isDone = False
                    print("$$ Collision $$")
                    print(f"val_key: {val_key}")
                    print(f"id: {id}")
                    collision_tasks += 1
                elif "SoftEps" in info:
                    print("$$ SoftEps $$")
                else:
                    successed_tasks += 1

    success_rate = successed_tasks / total_tasks * 100
    collision_rate = collision_tasks / total_tasks * 100
    results_dictionary["success_rate"] = success_rate
    results_dictionary["collision_rate"] = collision_rate
    results_dictionary["mean_constrained_cost"] = np.mean(constrained_cost)
    results_dictionary["max_constrained_cost"] = np.max(constrained_cost)
    results_dictionary["min_constrained_cost"] = np.min(constrained_cost)
    results_dictionary["mean_beam"] = np.mean(lst_min_beam)
    results_dictionary["min_beam"] = np.min(lst_min_beam)
    results_dictionary["max_beam"] = np.max(lst_min_beam)

    return results_dictionary

maps, trainTask, valTasks = dataSet["obstacles"]
environment_config = {
        'vehicle_config': car_config,
        'tasks': trainTask,
        'valTasks': valTasks,
        'maps': maps,
        'our_env_config' : our_env_config,
        'reward_config' : reward_config,
        'evaluation': {},
    }

train_env_name = "polamp_env-v0"
register(
    id=train_env_name,
    entry_point='polamp_env.lib.envs:POLAMPEnvironment',
    kwargs={'full_env_name': "polamp_env", "config": environment_config}
)

def parse_args():
    # fmt: off
    parser = argparse.ArgumentParser()
    parser.add_argument("--exp-name", type=str, default=os.path.basename(__file__).rstrip(".py"),
        help="the name of this experiment")
    parser.add_argument("--seed", type=int, default=1,
        help="seed of the experiment")
    parser.add_argument("--torch-deterministic", type=lambda x: bool(strtobool(x)), default=True, nargs="?", const=True,
        help="if toggled, `torch.backends.cudnn.deterministic=False`")
    parser.add_argument("--cuda", type=lambda x: bool(strtobool(x)), default=True, nargs="?", const=True,
        help="if toggled, cuda will be enabled by default")
    parser.add_argument("--track", type=lambda x: bool(strtobool(x)), default=False, nargs="?", const=True,
        help="if toggled, this experiment will be tracked with Weights and Biases")
    parser.add_argument("--wandb-project-name", type=str, default="cleanRL",
        help="the wandb's project name")
    parser.add_argument("--wandb-entity", type=str, default=None,
        help="the entity (team) of wandb's project")
    parser.add_argument("--capture-video", type=lambda x: bool(strtobool(x)), default=False, nargs="?", const=True,
        help="whether to capture videos of the agent performances (check out `videos` folder)")

    # Algorithm specific arguments
    parser.add_argument("--env-id", type=str, default="Hopper-v4",
        help="the id of the environment")
    parser.add_argument("--total-timesteps", type=int, default=1000000,
        help="total timesteps of the experiments")
    parser.add_argument("--validation-timesteps", type=int, default=20000,
        help="validation timesteps frequency")
    parser.add_argument("--buffer-size", type=int, default=int(1e6),
        help="the replay memory buffer size")
    parser.add_argument("--gamma", type=float, default=0.99,
        help="the discount factor gamma")
    parser.add_argument("--tau", type=float, default=0.005,
        help="target smoothing coefficient (default: 0.005)")
    parser.add_argument("--batch-size", type=int, default=256,
        help="the batch size of sample from the reply memory")
    parser.add_argument("--learning-starts", type=int, default=5e3,
        help="timestep to start learning")
    parser.add_argument("--policy-lr", type=float, default=3e-4,
        help="the learning rate of the policy network optimizer")
    parser.add_argument("--q-lr", type=float, default=1e-3,
        help="the learning rate of the Q network network optimizer")
    parser.add_argument("--policy-frequency", type=int, default=2,
        help="the frequency of training policy (delayed)")
    parser.add_argument("--target-network-frequency", type=int, default=1, # Denis Yarats' implementation delays this by 2.
        help="the frequency of updates for the target nerworks")
    parser.add_argument("--noise-clip", type=float, default=0.5,
        help="noise clip parameter of the Target Policy Smoothing Regularization")
    parser.add_argument("--alpha", type=float, default=0.2,
            help="Entropy regularization coefficient.")
    parser.add_argument("--autotune", type=lambda x:bool(strtobool(x)), default=True, nargs="?", const=True,
        help="automatic tuning of the entropy coefficient")
    args = parser.parse_args()
    # fmt: on
    return args


def make_env(env_id, seed, idx, capture_video, run_name):
    def thunk():
        env = gym.make(env_id)
        env = CustomRecordEpisodeStatistics(env)
        if capture_video:
            if idx == 0:
                env = gym.wrappers.RecordVideo(env, f"videos/{run_name}")
        env.seed(seed)
        env.action_space.seed(seed)
        env.observation_space.seed(seed)
        return env

    return thunk


# ALGO LOGIC: initialize agent here:
class SoftQNetwork(nn.Module):
    def __init__(self, env):
        super().__init__()
        self.fc1 = nn.Linear(np.array(env.single_observation_space.shape).prod() + np.prod(env.single_action_space.shape), 256)
        self.fc2 = nn.Linear(256, 256)
        self.fc3 = nn.Linear(256, 1)

    def forward(self, x, a):
        x = torch.cat([x, a], 1)
        x = F.relu(self.fc1(x))
        x = F.relu(self.fc2(x))
        x = self.fc3(x)
        return x


LOG_STD_MAX = 2
LOG_STD_MIN = -5


class Actor(nn.Module):
    def __init__(self, env):
        super().__init__()
        self.fc1 = nn.Linear(np.array(env.single_observation_space.shape).prod(), 256)
        self.fc2 = nn.Linear(256, 256)
        self.fc_mean = nn.Linear(256, np.prod(env.single_action_space.shape))
        self.fc_logstd = nn.Linear(256, np.prod(env.single_action_space.shape))
        # action rescaling
        self.register_buffer(
            "action_scale", torch.tensor((env.action_space.high - env.action_space.low) / 2.0, dtype=torch.float32)
        )
        self.register_buffer(
            "action_bias", torch.tensor((env.action_space.high + env.action_space.low) / 2.0, dtype=torch.float32)
        )

    def forward(self, x):
        x = F.relu(self.fc1(x))
        x = F.relu(self.fc2(x))
        mean = self.fc_mean(x)
        log_std = self.fc_logstd(x)
        log_std = torch.tanh(log_std)
        log_std = LOG_STD_MIN + 0.5 * (LOG_STD_MAX - LOG_STD_MIN) * (log_std + 1)  # From SpinUp / Denis Yarats

        return mean, log_std

    def get_action(self, x):
        mean, log_std = self(x)
        std = log_std.exp()
        normal = torch.distributions.Normal(mean, std)
        x_t = normal.rsample()  # for reparameterization trick (mean + std * N(0,1))
        y_t = torch.tanh(x_t)
        action = y_t * self.action_scale + self.action_bias
        log_prob = normal.log_prob(x_t)
        # Enforcing Action Bound
        log_prob -= torch.log(self.action_scale * (1 - y_t.pow(2)) + 1e-6)
        log_prob = log_prob.sum(1, keepdim=True)
        mean = torch.tanh(mean) * self.action_scale + self.action_bias
        return action, log_prob, mean


if __name__ == "__main__":
    args = parse_args()
    run_name = f"{args.env_id}__{args.exp_name}__{args.seed}__{int(time.time())}"
    if args.track:
        import wandb

        wandb.init(
            project=args.wandb_project_name,
            entity=args.wandb_entity,
            sync_tensorboard=True,
            config=vars(args),
            name=run_name,
            monitor_gym=True,
            save_code=True,
        )
    writer = SummaryWriter(f"runs/{run_name}")
    writer.add_text(
        "hyperparameters",
        "|param|value|\n|-|-|\n%s" % ("\n".join([f"|{key}|{value}|" for key, value in vars(args).items()])),
    )

    # TRY NOT TO MODIFY: seeding
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.backends.cudnn.deterministic = args.torch_deterministic

    device = torch.device("cuda" if torch.cuda.is_available() and args.cuda else "cpu")

    # env setup
    validation_env = gym.make(args.env_id)
    envs = gym.vector.SyncVectorEnv([make_env(args.env_id, args.seed, 0, args.capture_video, run_name)])
    assert isinstance(envs.single_action_space, gym.spaces.Box), "only continuous action space is supported"

    max_action = float(envs.single_action_space.high[0])

    actor = Actor(envs).to(device)
    qf1 = SoftQNetwork(envs).to(device)
    qf2 = SoftQNetwork(envs).to(device)
    qf1_target = SoftQNetwork(envs).to(device)
    qf2_target = SoftQNetwork(envs).to(device)
    qf1_target.load_state_dict(qf1.state_dict())
    qf2_target.load_state_dict(qf2.state_dict())
    q_optimizer = optim.Adam(list(qf1.parameters()) + list(qf2.parameters()), lr=args.q_lr)
    actor_optimizer = optim.Adam(list(actor.parameters()), lr=args.policy_lr)

    #Adding by Brian
    cost_limit = 0.5
    update_lambda = 1000
    qf1_cost = SoftQNetwork(envs).to(device)
    qf2_cost = SoftQNetwork(envs).to(device)
    qf1_target_cost = SoftQNetwork(envs).to(device)
    qf2_target_cost = SoftQNetwork(envs).to(device)
    qf1_target_cost.load_state_dict(qf1_cost.state_dict())
    qf2_target_cost.load_state_dict(qf2_cost.state_dict())
    q_optimizer_cost = optim.Adam(list(qf1_cost.parameters()) + list(qf2_cost.parameters()), lr=args.q_lr)
    lambda_coefficient = torch.tensor(1.0, requires_grad=True)
    lambda_optimizer = optim.Adam([lambda_coefficient], lr=5e-4)

    # Automatic entropy tuning
    if args.autotune:
        target_entropy = -torch.prod(torch.Tensor(envs.single_action_space.shape).to(device)).item()
        log_alpha = torch.zeros(1, requires_grad=True, device=device)
        alpha = log_alpha.exp().item()
        a_optimizer = optim.Adam([log_alpha], lr=args.q_lr)
    else:
        alpha = args.alpha

    envs.single_observation_space.dtype = np.float32
    rb = ReplayBuffer(
        args.buffer_size,
        envs.single_observation_space,
        envs.single_action_space,
        device,
        handle_timeout_termination=True,
    )
    start_time = time.time()

    # TRY NOT TO MODIFY: start the game
    obs = envs.reset()
    for global_step in range(args.total_timesteps):
        # ALGO LOGIC: put action logic here
        if global_step < args.learning_starts:
            actions = np.array([envs.single_action_space.sample() for _ in range(envs.num_envs)])
        else:
            actions, _, _ = actor.get_action(torch.Tensor(obs).to(device))
            actions = actions.detach().cpu().numpy()

        # TRY NOT TO MODIFY: execute the game and log data.
        next_obs, rewards, dones, infos = envs.step(actions)
        # print(f"rewards: {rewards}")
        # print(f"infos: {infos}")
        costs = infos[0].get('cost', 0)

        # TRY NOT TO MODIFY: record rewards for plotting purposes
        for info in infos:
            if "episode" in info.keys():
                print(f"global_step={global_step}, \
                      episodic_return={info['episode']['r']}, \
                      episodic_cost_return={info['episode']['c']}")
                writer.add_scalar("charts/episodic_return", info["episode"]["r"], global_step)
                writer.add_scalar("charts/episodic_length", info["episode"]["l"], global_step)
                writer.add_scalar("charts/episodic_cost_return", info["episode"]["c"], global_step)
                break

        # TRY NOT TO MODIFY: save data to reply buffer; handle `terminal_observation`
        real_next_obs = next_obs.copy()
        for idx, d in enumerate(dones):
            if d:
                real_next_obs[idx] = infos[idx]["terminal_observation"]
        rb.add(obs, real_next_obs, actions, rewards, costs, dones, infos)

        # TRY NOT TO MODIFY: CRUCIAL step easy to overlook
        obs = next_obs

        # ALGO LOGIC: training.
        if global_step > args.learning_starts:
            data = rb.sample(args.batch_size)
            with torch.no_grad():
                next_state_actions, next_state_log_pi, _ = actor.get_action(data.next_observations)
                qf1_next_target = qf1_target(data.next_observations, next_state_actions)
                qf2_next_target = qf2_target(data.next_observations, next_state_actions)
                min_qf_next_target = torch.min(qf1_next_target, qf2_next_target) - alpha * next_state_log_pi
                next_q_value = data.rewards.flatten() + (1 - data.dones.flatten()) * args.gamma * (min_qf_next_target).view(-1)
                
                # Adding by Brian
                qf1_next_target_cost = qf1_target_cost(data.next_observations, next_state_actions)
                qf2_next_target_cost = qf2_target_cost(data.next_observations, next_state_actions)
                min_qf_next_target_cost = torch.min(qf1_next_target_cost, qf2_next_target_cost) - alpha * next_state_log_pi
                next_q_value_cost = data.costs.flatten() + (1 - data.dones.flatten()) * args.gamma * (min_qf_next_target_cost).view(-1)

            qf1_a_values = qf1(data.observations, data.actions).view(-1)
            qf2_a_values = qf2(data.observations, data.actions).view(-1)
            qf1_loss = F.mse_loss(qf1_a_values, next_q_value)
            qf2_loss = F.mse_loss(qf2_a_values, next_q_value)
            qf_loss = qf1_loss + qf2_loss

            q_optimizer.zero_grad()
            qf_loss.backward()
            q_optimizer.step()

            # Adding by Brian
            qf1_a_values_cost = qf1_cost(data.observations, data.actions).view(-1)
            qf2_a_values_cost = qf2_cost(data.observations, data.actions).view(-1)
            qf1_loss_cost = F.mse_loss(qf1_a_values_cost, next_q_value_cost)
            qf2_loss_cost = F.mse_loss(qf2_a_values_cost, next_q_value_cost)
            qf_loss_cost = qf1_loss_cost + qf2_loss_cost
            q_optimizer_cost.zero_grad()
            qf_loss_cost.backward()
            q_optimizer_cost.step()
            
            if global_step % args.policy_frequency == 0:  # TD 3 Delayed update support
                for _ in range(
                    args.policy_frequency
                ):  # compensate for the delay by doing 'actor_update_interval' instead of 1
                    pi, log_pi, _ = actor.get_action(data.observations)
                    qf1_pi = qf1(data.observations, pi)
                    qf2_pi = qf2(data.observations, pi)
                    min_qf_pi = torch.min(qf1_pi, qf2_pi).view(-1)
                    # actor_loss = ((alpha * log_pi) - min_qf_pi).mean()
                    
                    # Adding by Brian
                    lambda_multiplier = torch.nn.functional.softplus(lambda_coefficient)
                    qf1_pi_cost = qf1_cost(data.observations, pi)
                    qf2_pi_cost = qf2_cost(data.observations, pi)
                    min_qf_pi_cost = lambda_multiplier * torch.min(qf1_pi_cost, qf2_pi_cost).view(-1)

                    actor_loss = ((alpha * log_pi) - min_qf_pi + min_qf_pi_cost).mean()

                    actor_optimizer.zero_grad()
                    actor_loss.backward()
                    actor_optimizer.step()

                    if args.autotune:
                        with torch.no_grad():
                            _, log_pi, _ = actor.get_action(data.observations)
                        alpha_loss = (-log_alpha * (log_pi + target_entropy)).mean()

                        a_optimizer.zero_grad()
                        alpha_loss.backward()
                        a_optimizer.step()
                        alpha = log_alpha.exp().item()
            
                torch.save(actor.state_dict(), f'runs/{run_name}/actor.pkl')
            #Lagrangian
            if global_step % update_lambda:
                qf1_a_values_cost = qf1_cost(data.observations, data.actions).view(-1)
                qf2_a_values_cost = qf2_cost(data.observations, data.actions).view(-1)
                violation = torch.min(qf1_a_values_cost, qf2_a_values_cost) - cost_limit
                # log_lam = torch.nn.functional.softplus(lambda_coefficient)
                lambda_loss =  lambda_coefficient * violation.detach()
                lambda_loss = -lambda_loss.sum(dim=-1)
                lambda_optimizer.zero_grad()
                lambda_loss.backward()
                lambda_optimizer.step()
        
            if global_step % args.validation_timesteps == 0:
                results_dictionary = validation(validation_env, actor)
                for key in results_dictionary:
                    writer.add_scalar("validation/" + key, results_dictionary[key], global_step)

            # update the target networks
            if global_step % args.target_network_frequency == 0:
                for param, target_param in zip(qf1.parameters(), qf1_target.parameters()):
                    target_param.data.copy_(args.tau * param.data + (1 - args.tau) * target_param.data)
                for param, target_param in zip(qf2.parameters(), qf2_target.parameters()):
                    target_param.data.copy_(args.tau * param.data + (1 - args.tau) * target_param.data)

            if global_step % 100 == 0:
                writer.add_scalar("losses/qf1_values", qf1_a_values.mean().item(), global_step)
                writer.add_scalar("losses/qf2_values", qf2_a_values.mean().item(), global_step)
                writer.add_scalar("losses/qf1_values_cost", qf1_a_values_cost.mean().item(), global_step)
                writer.add_scalar("losses/qf2_values_cost", qf2_a_values_cost.mean().item(), global_step)
                writer.add_scalar("losses/qf1_loss", qf1_loss.item(), global_step)
                writer.add_scalar("losses/qf2_loss", qf2_loss.item(), global_step)
                writer.add_scalar("losses/qf1_loss_cost", qf1_loss_cost.item(), global_step)
                writer.add_scalar("losses/qf2_loss_cost", qf2_loss_cost.item(), global_step)
                writer.add_scalar("losses/qf_loss", qf_loss.item() / 2.0, global_step)
                writer.add_scalar("losses/qf_loss_cost", qf_loss_cost.item() / 2.0, global_step)
                writer.add_scalar("losses/actor_loss", actor_loss.item(), global_step)
                writer.add_scalar("losses/alpha", alpha, global_step)
                writer.add_scalar("losses/lambda", lambda_multiplier, global_step)
                print("SPS:", int(global_step / (time.time() - start_time)))
                writer.add_scalar("charts/SPS", int(global_step / (time.time() - start_time)), global_step)
                if args.autotune:
                    writer.add_scalar("losses/alpha_loss", alpha_loss.item(), global_step)

    envs.close()
    writer.close()