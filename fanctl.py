#!/usr/bin/env python3
"""
Raspberry Pi Fan Controller
https://github.com/process1183/rpi-fanctl

Copyright (C) 2024  Josh Gadeken

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""
import argparse
import configparser
import logging
import os
import signal
import threading
import time

import pigpio # http://abyz.me.uk/rpi/pigpio/python.html

DEFAULT_CONFIG_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)), "fanctl.conf")

DEFAULT_PARAMETERS = {
    "cpu_temp_file": "/sys/class/thermal/thermal_zone0/temp",
    "cpu_temp_max": 80.0,
    "cpu_temp_sample_count": 5,
    "cpu_temp_sample_delay": 0.1,
    "fan_active_min_speed": 20,
    "fan_pwm_pin": 13,
    "hysteresis": 5.0,
    "trigger_temp": 50.0,
}


class PWMFan:
    """Control a cooling fan via PWM"""
    def __init__(self,
                 rpi: pigpio.pi,
                 pwm_pin: int,
                 pwm_freq: int = 25000) -> None:
        """
        Args:
            rpi: pigpio.pi instance
            pwm_pin: GPIO pin number that the fan PWM control is connected to
            pwm_freq: Frequency to drive the fan PWM signal. The default of 25kHz
                      is the ideal frequency for Noctua fans (see page 6 of the
                      Noctua PWM specifications white paper).

        Returns:
            None
        """
        self._rpi = rpi
        self.pwm_pin = pwm_pin
        self.pwm_freq = pwm_freq
        self._speed_percent = 0
        self.speed = 0

    @property
    def speed(self) -> int:
        """Get the current fan speed percent.

        Args:
            None

        Returns:
            The fan speed percent (0-100)
        """
        return self._speed_percent

    @speed.setter
    def speed(self, speed_percent: int) -> None:
        """Set the fan speed percent.

        Args:
            speed_percent: The fan speed percent (0-100)

        Returns:
            None
        """
        if not 0 <= speed_percent <= 100:
            raise ValueError("Invalid fan speed!")

        self._speed_percent = speed_percent

        # http://abyz.me.uk/rpi/pigpio/python.html#hardware_PWM
        dutycycle = self._speed_percent * 10000
        self._rpi.hardware_PWM(self.pwm_pin, self.pwm_freq, dutycycle)


class CPUTemp:
    """Easily read the system CPU temperature.

    Note: This wrapper class is used instead of `with open(...) as ...`
          so that the CPU temperature file isn't being constantly opened
          and closed multiple times per second.
    """
    def __init__(self, temperature_file: str) -> None:
        """
        Args:
            temperature_file: Path of the CPU temperature file.
                              E.g. `/sys/class/thermal/thermal_zone0/temp`

        Returns:
            None
        """
        self.filename = temperature_file
        self._fd = open(self.filename, 'r', encoding="utf-8")

    def read(self) -> float:
        """Read the current CPU temperature.

        Args:
            None

        Returns:
            Current CPU temperature in degrees Celsius
        """
        _ = self._fd.seek(0)
        return float(self._fd.read()) / 1000.0

    def __del__(self) -> None:
        """Close the CPU temperature file

        Args:
            None

        Returns:
            None
        """
        self._fd.close()


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Control a PWM fan based on CPU temperature.")

    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose output."
    )

    parser.add_argument(
        "-c", "--config", action="store", default=DEFAULT_CONFIG_FILE,
        help=f"Path to the configuration file. By default, this script will look for '{DEFAULT_CONFIG_FILE}'. If the config file is not found, then the built-in defaults will be used."
    )

    return parser.parse_args()



def load_config(config_file_path: str, defaults: dict) -> dict:
    """Load configuration parameters.

    Args:
        config_file_path: Path to the configuration file
        defaults: dict that contains the default configuration parameters

    Returns:
        Configuration dict
    """
    config = configparser.ConfigParser(defaults=defaults)

    # The fanctl.conf file does not use sections for simplicity, but
    # configparser requires at least one section... fake it with `defsec`
    defsec = "DEFAULT"

    with open(config_file_path, 'r', encoding="utf-8") as inf:
        config.read_string(f"[{defsec}]\n" + inf.read())

    # Convert the configparser to a simple dict.
    # All configparser values are strings, so convert floats and ints
    # based on the value types in `defaults`.
    cfg = {}
    for key, value in config.items(defsec):
        if isinstance(defaults[key], float):
            cfg[key] = config.getfloat(defsec, key)
        elif isinstance(defaults[key], int):
            cfg[key] = config.getint(defsec, key)
        else:
            cfg[key] = value

    return cfg


def clamped_map(x: float,
                in_min: float,
                in_max: float,
                out_min: float,
                out_max: float) -> float:
    """Re-maps a number from one range to another.

    This is a modified copy of Arduino's map() function:
    https://www.arduino.cc/reference/en/language/functions/math/map/

    This version clamps (constrains) the output to `out_min` or `out_max`
    if `x` is less than `in_min` or greater than `in_max`.

    Args:
        x: The number to map.
        in_min: The lower bound of the value's current range.
        in_max: The upper bound of the value's current range.
        out_min: The lower bound of the value's target range.
        out_max: The upper bound of the value's target range.

    Returns:
        The mapped value.
    """
    if x < in_min:
        return out_min

    if x > in_max:
        return out_max

    return (x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min


def control_fan(cpu_temp: CPUTemp, fan: PWMFan, params: dict) -> None:
    """Control the cooling fan based on the current CPU temperature.

    While the CPU temp is below the trigger temp
    (`params["trigger_temp"]`), the fan will be off. If the CPU temp
    reaches the trigger temp, then the fan is started at
    `params["fan_active_min_speed"]`, and the trigger temp is then
    reduced by `params["hysteresis"]` until the CPU temp falls below
    the temporary new trigger temp. While the CPU temp is above the
    trigger temp, the fan speed will be proportionally adjusted based
    on the CPU temp. If the CPU temp exceeds `params["cpu_temp_max"]`,
    then the fan will spin at 100%.

    Args:
        cpu_temp: CPUTemp instance
        fan: PWMFan instance
        params: Dictionary containing the various control parameters

    Returns:
        None
    """
    # Average several samples to smooth out temp reading
    temps = []
    for _ in range(params["cpu_temp_sample_count"]):
        temps.append(round(cpu_temp.read()))
        time.sleep(params["cpu_temp_sample_delay"])

    temp = round(sum(temps) / len(temps))
    logging.info("CPU temp samples: %s", temps)
    logging.info("Averaged CPU temp: %s", temp)

    # If the fan is currently active, set the deactivation trigger temp
    # to a slightly lower temperature to prevent the fan activation/deactivation
    # from rapidly oscillating around the specified trigger temp.
    trigger_temp = params["trigger_temp"]
    if fan.speed != 0:
        trigger_temp -= params["hysteresis"]

    logging.info("Current fan speed percent: %s, Trigger: %sC", fan.speed, trigger_temp)

    if temp < trigger_temp:
        fan.speed = 0
        logging.info("Set fan speed to 0%")
    else:
        fanspeed = clamped_map(
            temp,
            trigger_temp,
            params["cpu_temp_max"],
            params["fan_active_min_speed"],
            100.0
        )

        fan.speed = int(fanspeed)
        logging.info("Set fan speed percent to %s", fan.speed)


def main(args: argparse.Namespace) -> None:
    """Continuously adjust the fan speed based on CPU temperature.

    Args:
        args: Parsed command line args from argparse.

    Returns:
        None
    """
    logging.basicConfig(
        format="[%(asctime)s] %(levelname)s: %(message)s (%(filename)s:%(lineno)s)",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=logging.INFO if args.verbose else logging.ERROR
    )

    # Read any overridden parameters from the file specified by `args.config`.
    try:
        params = load_config(args.config, DEFAULT_PARAMETERS)
    except Exception as err:
        logging.error("Unable to read '%s', using built-in defaults. (%s)", args.config, err)
        params = DEFAULT_PARAMETERS

    logging.info("parameters: %s", params)

    rpi = pigpio.pi()
    cpu_temp = CPUTemp(params["cpu_temp_file"])
    fan = PWMFan(rpi, params["fan_pwm_pin"])

    shutdown_event = threading.Event()
    signal.signal(signal.SIGINT, lambda signum, frame: shutdown_event.set())
    signal.signal(signal.SIGTERM, lambda signum, frame: shutdown_event.set())

    # Adjust main loop sleep() based on number of CPU temp readings
    temp_sample_time = params["cpu_temp_sample_delay"] * params["cpu_temp_sample_count"]
    main_loop_delay = 1.0 - temp_sample_time if temp_sample_time < 1.0 else 0.01

    while not shutdown_event.is_set():
        control_fan(cpu_temp, fan, params)
        time.sleep(main_loop_delay)



if __name__ == "__main__":
    main(parse_args())
