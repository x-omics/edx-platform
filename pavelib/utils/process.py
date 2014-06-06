"""
Helper functions for managing processes.
"""
from __future__ import print_function
import sys
import os
import subprocess
import signal
import psutil


def kill_process(proc):
    """
    Kill the process `proc` created with `subprocess`.
    """
    p1_group = psutil.Process(proc.pid)

    child_pids = p1_group.get_children(recursive=True)

    for child_pid in child_pids:
        os.kill(child_pid.pid, signal.SIGKILL)


def run_multi_processes(cmd_list, out_log=None, err_log=None, background=False, cwd=None):
    """
    Run each shell command in `cmd_list` in a separate process,
    piping stdout to `out_log` (a path) and stderr to `err_log` (also a path).

    Terminates the processes on CTRL-C and ensures the processes are killed
    if an error occurs.
    """
    kwargs = {'shell': True, 'cwd': cwd}
    pids = []

    if out_log:
        out_log_file = open(out_log, 'w')
        kwargs['stdout'] = out_log_file

    if err_log:
        err_log_file = open(err_log, 'w')
        kwargs['stderr'] = err_log_file

    try:
        for cmd in cmd_list:
            pids.extend([subprocess.Popen(cmd, **kwargs)])

        if not background:
            def _signal_handler():
                """
                What to do when process is ended
                """
                print("\nEnding...")

            signal.signal(signal.SIGINT, _signal_handler)
            print("Enter CTL-C to end")
            signal.pause()
            print("Processes ending")

    except Exception as err:  # pylint: disable-msg=broad-except
        print("Error running process {}".format(err), file=sys.stderr)

    finally:
        if not background:
            for pid in pids:
                kill_process(pid)

    return pids


def run_process(cmd, out_log=None, err_log=None, cwd=None):
    """
    Run the shell command `cmd` in a separate process,
    piping stdout to `out_log` (a path) and stderr to `err_log` (also a path).

    Terminates the process on CTRL-C or if an error occurs.
    """
    return run_multi_processes([cmd], out_log=out_log, err_log=err_log, cwd=cwd)


def run_background_process(cmd, out_log=None, err_log=None, cwd=None):
    """
    Runs a command as a background process. Note you will have to kill the processes
    explicitly when you are done with them, so the pids are returned.
    """
    return run_multi_processes([cmd], out_log=out_log, err_log=err_log, background=True, cwd=cwd)
