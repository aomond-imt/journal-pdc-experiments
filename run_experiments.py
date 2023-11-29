import math
import os
import shutil
import traceback
from contextlib import redirect_stdout
from multiprocessing import cpu_count, shared_memory, Process
import time
from os.path import exists

import esds
import yaml
from execo_engine import ParamSweeper, sweep

import shared_methods
from topologies import clique, chain, ring, star, grid, tasks_list_agg_0, tasks_list_grid_fav, tasks_list_agg_middle

tasks_list_tplgy = {
    "star-fav": (tasks_list_agg_0, star),
    "star-nonfav": (tasks_list_agg_middle, star),
    "ring-fav": (tasks_list_agg_0, ring),
    "chain-fav": (tasks_list_agg_middle, chain),
    "chain-nonfav": (tasks_list_agg_0, chain),
    "clique-fav": (tasks_list_agg_0, clique),
    "grid-fav": (tasks_list_grid_fav, grid),
    "grid-nonfav": (tasks_list_agg_0, grid),
}


def run_simulation(test_expe, sweeper):
    parameters = sweeper.get_next()
    while parameters is not None:
        root_results_dir = f"{os.environ['HOME']}/results-reconfiguration-esds/topologies/{['paper', 'tests'][test_expe]}"
        results_dir = f"{parameters['tplgy_name']}-{parameters['nodes_count']}/{parameters['id_run']}"
        expe_results_dir = f"{root_results_dir}/{results_dir}"
        debug_file_dir = f"{shared_methods.TMP_DIR}/{results_dir}"
        os.makedirs(expe_results_dir, exist_ok=True)
        os.makedirs(debug_file_dir, exist_ok=True)
        debug_file_path = f"{debug_file_dir}/debug.txt"

        try:
            # Setup parameters
            nodes_count = parameters["nodes_count"]
            tasks_list, tplgy_func = tasks_list_tplgy[parameters["tplgy_name"]]
            B, L = tplgy_func(nodes_count, shared_methods.BANDWIDTH)
            smltr = esds.Simulator({"eth0": {"bandwidth": B, "latency": L, "is_wired": False}})

            if not test_expe:
                uptimes_schedule_name = f"uptimes_schedules/{parameters['id_run']}-{shared_methods.UPT_DURATION}.json"
            else:
                uptimes_schedule_name = f"expes-tests/{parameters['tplgy_name']}.json"
                if not exists(uptimes_schedule_name):
                    print(f"No test found for {parameters['tplgy_name']}")
                    continue

            node_arguments = {
                "results_dir": expe_results_dir,
                "nodes_count": nodes_count,
                "uptimes_schedule_name": uptimes_schedule_name,
                "tasks_list": tasks_list(nodes_count - 1),
                "topology": B,
                "s": shared_memory.SharedMemory(f"shm_cps_{time.time_ns()}", create=True, size=nodes_count)
            }

            # Setup and launch simulation
            print(f"Starting {parameters}")
            start_time = time.perf_counter()
            for node_num in range(nodes_count):
                smltr.create_node("on_pull", interfaces=["eth0"], args=node_arguments)
            with open(debug_file_path, "w") as f:
                with redirect_stdout(f):
                    smltr.run(interferences=False)
            node_arguments["s"].close()
            try:
                node_arguments["s"].unlink()
            except FileNotFoundError as e:
                traceback.print_exc()

            # If test, verification
            if test_expe:
                with open(f"expes-tests/{parameters['tplgy_name']}.yaml") as f:
                    expected_results = yaml.safe_load(f)["expected_result"]
                errors = shared_methods.verify_results(expected_results, expe_results_dir)
                if len(errors) == 0:
                    print(f"{results_dir}: ok")
                else:
                    print(f"{results_dir}: errors: \n" + "\n".join(errors))
            else:
                print(f"{results_dir}: done in {round(time.perf_counter() - start_time, 2)}s")

            # Go to next parameter
            sweeper.done(parameters)
        except Exception as exc:
            traceback.print_exc()
            sweeper.skip(parameters)
        finally:
            if exists(debug_file_path):
                shutil.copy(debug_file_path, expe_results_dir)
                os.remove(debug_file_path)
            parameters = sweeper.get_next()


def main():
    test_expe = True
    if test_expe:
        print("Testing")
    else:
        print("Simulation start")

    parameter_list = {
        "tplgy_name": [
            "star-fav",
            "star-nonfav",
            "ring-fav",
            "chain-fav",
            "chain-nonfav",
            "clique-fav",
            "grid-nonfav",
            "grid-fav",
        ],
        "nodes_count": [9, 16, 25],
        "id_run": [*range(30)],
    }

    # Create parameters list/sweeper
    if not test_expe:
        persistence_dir = f"{shared_methods.HOME_DIR}/optim-esds-sweeper"
        sweeps = sweep(parameter_list)
    else:
        persistence_dir = f"{shared_methods.TMP_DIR}/test-{int(time.time())}"
        sweeps = sweep({"tplgy_name": parameter_list["tplgy_name"], "nodes_count": [6], "id_run": [0]})

    # Sweeper read/write is thread-safe even on NFS (https://mimbert.gitlabpages.inria.fr/execo/execo_engine.html?highlight=paramsweeper#execo_engine.sweep.ParamSweeper)
    sweeper = ParamSweeper(
        persistence_dir=persistence_dir, sweeps=sweeps, save_sweeps=True
    )

    nb_cores = math.ceil(cpu_count() * 0.5)
    processes = []
    for _ in range(nb_cores):
        p = Process(target=run_simulation, args=(test_expe, sweeper))
        p.start()
        processes.append(p)
    for p in processes:
        p.join()


if __name__ == "__main__":
    main()
