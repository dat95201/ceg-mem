"""A2 - run candidate code on one input under a hard timeout.

QuixBugs is full of recursion and loops; a wrong patch hangs easily. Run in a
subprocess, cap wall time, and catch every exception including SystemExit.
"""
