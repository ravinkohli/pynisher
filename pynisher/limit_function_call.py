#! /bin/python
import resource
import signal
# supports spawning processes using an API
import multiprocessing
# using operating system dependent functionality
import os

class abort_function (Exception): pass


# create the function the subprocess can execute
def subprocess_func(func, pipe, mem_in_mb, cpu_time_limit_in_s, wall_time_limit_in_s, num_procs, *args, **kwargs):
    # returning logger used by multiprocessing (a new one is created)
    logger = multiprocessing.get_logger()
    # Return the id of the current process group.
    os.setpgrp()

    # simple signal handler to catch the signals for time limits
    def handler(signum, frame):
        # logs message with level debug on this logger 
        logger = multiprocessing.get_logger()
        logger.debug("received signal number %i. Exiting uncracefully."%signum)
        
        if (signum == signal.SIGXCPU):
            # when process reaches soft limit --> a SIGXCPU signal is sent (it normally terminats the process)
            logger.warning("CPU time exceeded, aborting!")
        elif (signum == signal.SIGALRM):
            # SIGALRM is sent to process when the specified time limit to an alarm function elapses (when real or clock time elapses)
            logger.warning("Wallclock time exceeded, aborting!")
        raise abort_function
    

    # catch all catchable signals
    for i in [x for x in dir(signal) if x.startswith("SIG")]:
        try:
            signum = getattr(signal,i)
            signal.signal(signum,handler)
        except: # ignore problems setting the handle, surely an uncatchable signum :)
            pass

    # set the memory limit
    if mem_in_mb is not None:
        # byte --> megabyte
        mem_in_b = mem_in_mb*1024*1024
        # the maximum area (in bytes) of address space which may be taken by the process.
        resource.setrlimit(resource.RLIMIT_AS, (mem_in_b, mem_in_b))

    # for now: don't allow the function to spawn subprocesses itself.
    #resource.setrlimit(resource.RLIMIT_NPROC, (1, 1))
    # Turns out, this is quite restrictive, so we don't use this option by default
    if num_procs is not None:
        resource.setrlimit(resource.RLIMIT_NPROC, (num_procs, num_procs))


    # schedule an alarm in specified number of seconds
    if wall_time_limit_in_s is not None:
        signal.alarm(wall_time_limit_in_s)
    
    if cpu_time_limit_in_s is not None:
        # one could also limit the actual CPU time, but that does not help if the process hangs, e.g., in a dead-lock
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_time_limit_in_s,cpu_time_limit_in_s))


    return_value = None
    # the actual function call
    try:
        logger.debug('call to your function')
        return_value = func(*args, **kwargs)
        logger.debug('function returned %s'%str(return_value))

    except MemoryError:
        logger.warning("Function call with the arguments {}, {} has exceeded the memory limit!".format(args,kwargs))

    except OSError as e:
        if (e.errno == 11):
            logger.warning("Your function tries to spawn too many subprocesses/threads.")
        else:
            logger.debug('Something is going on here!')
            raise;

    except abort_function:
        return_value = None
        logger.warning('Your function call was aborted.')

    except:
        logger.debug('The call to your function did not return properly!\n%s\n%s', args, kwargs)
        raise;
    finally:
        try:
            pipe.send(return_value)
            pipe.close()
        except:
            pass


def enforce_limits (mem_in_mb=None, cpu_time_in_s=None, wall_time_in_s=None, num_processes=None, grace_period_in_s = None):

    logger = multiprocessing.get_logger()
    
    if mem_in_mb is not None:
        logger.debug("restricting your function to {} mb memory.".format(mem_in_mb))
    if cpu_time_in_s is not None:
        logger.debug("restricting your function to {} seconds cpu time.".format(cpu_time_in_s))
    if wall_time_in_s is not None:
        logger.debug("restricting your function to {} seconds wall time.".format(wall_time_in_s))
    if num_processes is not None:
        logger.debug("restricting your function to {} threads/processes.".format(num_processes))
    if grace_period_in_s is None:
        grace_period_in_s = 0

    
    def actual_decorator(func):

        def wrapped_function(*args, **kwargs):
            global return_value
            logger = multiprocessing.get_logger()
            
            # create a pipe to retrieve the return value
            parent_conn, child_conn = multiprocessing.Pipe()

            # create and start the process
            subproc = multiprocessing.Process(target=subprocess_func, name="Call to your function", args = (func, child_conn,mem_in_mb, cpu_time_in_s, wall_time_in_s, num_processes) + args ,kwargs = kwargs)
            logger.debug("Your function is called now.")

            return_value = None

            def child_died(signum, frame):
                logger.debug("Subprocess running your function has died!")
                if subproc.is_alive():
                    subproc.join()
                if parent_conn.poll():
                    global return_value
                    return_value = parent_conn.recv()
                parent_conn.close()

            signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGCHLD})
            signal.signal(signal.SIGCHLD, child_died)

            try:            
                subproc.start()
                if wall_time_in_s is not None:
                    # politely wait for it to finish
                    ready = parent_conn.poll(wall_time_in_s)
                
                    # if it is still alive, send sigterm for clean up
                    # and return None to signal that it did not finish
                    if not ready:
                        logger.debug("Your function took to long, killing it now.")
                        try:
                            os.killpg(os.getpgid(subproc.pid),15)
                            time.sleep(grace_period_in_s)
                            logger.debug("Killing succesful!")
                        except:
                            logger.debug("Killing the function call failed. It probably finished already.")
                        finally:
                            return(None)
                    # at this point there is something to be received
                    return_value = parent_conn.recv()
                    logger.debug("Your function has returned now with exit code %i."%subproc.exitcode)
                
                    # if something went wrong, 
                    if subproc.exitcode != 0:
                        logger.debug("Exit code was not 0 -> return value set to None!."%subproc.exitcode)
                        return_value = None
                else:
                    signal.pause()

            except: # reraise everything else
                raise

            finally:
                return (return_value); 
        return wrapped_function
    return actual_decorator
