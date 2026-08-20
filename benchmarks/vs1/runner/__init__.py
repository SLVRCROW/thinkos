"""VS-1 powered runner package.

Minimum experimental execution layer for the frozen VS-1 study.
Live provider calls happen ONLY here — never in the frozen measurement
package (benchmarks/vs1/*.py), whose no-network gate stays intact.
"""
