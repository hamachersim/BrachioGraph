# coding=utf-8

from time import sleep
import readchar
import math
import numpy
import json

try:
    import pigpio
    force_virtual_mode = False
except ModuleNotFoundError:
    print("pigpio not installed, running in test mode")
    force_virtual_mode = True

import tqdm


class BrachioGraph:

    def __init__(
        self,
        inner_arm,                  # the lengths of the arms
        outer_arm,
        virtual_mode = False,
        bounds=None,                # the maximum rectangular drawing area
        servo_1_angle_pws=[],       # pulse-widths for various angles
        servo_2_angle_pws=[],
        servo_1_zero=1500,
        servo_2_zero=1500,
        servo_1_degree_ms = -10,    # milliseconds pulse-width per degree
        servo_2_degree_ms = 10,     # reversed because for mpunting of the elbow servo
        pw_up=1500,                 # pulse-widths for pen up/down
        pw_down=1100,
    ):


        # set the pantograph geometry
        self.INNER_ARM = inner_arm
        self.OUTER_ARM = outer_arm

        self.virtual_mode = virtual_mode or force_virtual_mode

        # the box bounds describe a rectangle that we can safely draw in
        self.bounds = bounds

        # if pulse-widths to angles are supplied for each servo, we will feed them to
        # numpy.polyfit(), to produce a function for each one. Otherwise, we will use a simple
        # approximation based on a centre of travel of 1500µS and 10µS per degree

        if servo_1_angle_pws:
            servo_1_array = numpy.array(servo_1_angle_pws)
            self.angles_to_pw_1 = numpy.poly1d(
                numpy.polyfit(
                    servo_1_array[:,0],
                    servo_1_array[:,1],
                    3
                )
            )

        else:
            self.angles_to_pw_1 = self.naive_angles_to_pulse_widths_1
            self.servo_1_zero = servo_1_zero
            self.servo_1_degree_ms = servo_1_degree_ms

        if servo_2_angle_pws:
            servo_2_array = numpy.array(servo_2_angle_pws)
            self.angles_to_pw_2 = numpy.poly1d(
                numpy.polyfit(
                    servo_2_array[:,0],
                    servo_2_array[:,1],
                    3
                )
            )

        else:
            self.angles_to_pw_2 = self.naive_angles_to_pulse_widths_2
            self.servo_2_zero = servo_2_zero
            self.servo_2_degree_ms = servo_2_degree_ms

        if self.virtual_mode:

            print("Initialising virtual BrachioGraph")

            # create the pen object, and make sure the pen is up
            self.pen = Pen(bg=self, pw_up=pw_up, pw_down=pw_down)
            self.virtual_pw_1 = self.angles_to_pw_1(-90)
            self.virtual_pw_2 = self.angles_to_pw_2(90)

            print("    Pen is up")
            print("    Pulse-width 1", self.virtual_pw_1)
            print("    Pulse-width 2", self.virtual_pw_2)

        else:

            # instantiate this Raspberry Pi as a pigpio.pi() instance
            self.rpi = pigpio.pi()

            # the pulse frequency should be no higher than 100Hz - higher values could (supposedly) damage the servos
            self.rpi.set_PWM_frequency(14, 50)
            self.rpi.set_PWM_frequency(15, 50)

            # create the pen object, and make sure the pen is up
            self.pen = Pen(bg=self, pw_up=pw_up, pw_down=pw_down)

            # Initialise the pantograph with the motors in the centre of their travel
            self.rpi.set_servo_pulsewidth(14, self.angles_to_pw_1(-90))
            sleep(0.3)
            self.rpi.set_servo_pulsewidth(15, self.angles_to_pw_2(90))
            sleep(0.3)

        # Now the plotter is in a safe physical state.

        # Set the x and y position state, so it knows its current x/y position.
        self.current_x = -self.INNER_ARM
        self.current_y = self.OUTER_ARM

        # Create sets for recording movement of the plotter.
        self.angles_used_1 = set()
        self.angles_used_2 = set()
        self.pulse_widths_used_1 = set()
        self.pulse_widths_used_2 = set()


    # ----------------- drawing methods -----------------


    def plot_file(self, filename="", wait=.1, interpolate=10, bounds=None, pre_start=False):

        bounds = bounds or self.bounds

        if not bounds:
            return "File plotting is only possible when BrachioGraph.bounds is set."

        with open(filename, "r") as line_file:
            lines = json.load(line_file)

        self.plot_lines(lines=lines, wait=wait, interpolate=interpolate, pre_start=pre_start, bounds=bounds, flip=True)


    def plot_lines(self, lines=[], wait=.1, interpolate=10, pre_start=False, rotate=False, flip=False, bounds=None):

        bounds = bounds or self.bounds

        if not bounds:
            return "Line plotting is only possible when BrachioGraph.bounds is set."

        # lines is a tuple itself containing a number of tuples, each of which contains a number of 2-tuples
        #
        # [                                                                                     # |
        #     [                                                                                 # |
        #         [3, 4],                               # |                                     # |
        #         [2, 4],                               # |                                     # |
        #         [1, 5],  #  a single point in a line  # |  a list of points defining a line   # |
        #         [3, 5],                               # |                                     # |
        #         [3, 7],                               # |                                     # |
        #     ],                                                                                # |
        #     [                                                                                 # |  all the lines
        #         [...],                                                                        # |
        #         [...],                                                                        # |
        #     ],                                                                                # |
        #     [                                                                                 # |
        #         [...],                                                                        # |
        #         [...],                                                                        # |
        #     ],                                                                                # |
        # ]                                                                                     # |

        # First, we create a pair of empty sets for all the x and y values in all of the lines of the plot data.

        x_values_in_lines = set()
        y_values_in_lines = set()

        # Loop over each line and all the points in each line, to get sets of all the x and y values:

        for line in lines:

            x_values_in_line, y_values_in_line = zip(*line)

            x_values_in_lines.update(x_values_in_line)
            y_values_in_lines.update(y_values_in_line)

        # Identify the minimum and maximum values.

        min_x, max_x = min(x_values_in_lines), max(x_values_in_lines)
        min_y, max_y = min(y_values_in_lines), max(y_values_in_lines)

        # Identify the range they span.

        x_range = max_x - min_x
        y_range = max_y - min_y

        x_mid_point = (max_x + min_x) / 2
        y_mid_point = (max_y + min_y) / 2

        box_x_range = bounds[2] - bounds[0]
        box_y_range = bounds[3] - bounds[1]

        box_x_mid_point = (bounds[0] + bounds[2]) / 2
        box_y_mid_point = (bounds[1] + bounds[3]) / 2

        # Get a 'divider' value for each range - the value by which we must divide all x and y so that they will
        # fit safely inside the drawing range of the plotter.

        #
        # If both image and box are in portrait orientation, or both in landscape, we don't need to rotate the plot.

        if (x_range >= y_range and box_x_range >= box_y_range) or (x_range <= y_range and box_x_range <= box_y_range):

            divider = max((x_range / box_x_range), (y_range / box_y_range))
            rotate = False

        else:

            divider = max((x_range / box_y_range), (y_range / box_x_range))
            rotate = True
            x_mid_point, y_mid_point = y_mid_point, x_mid_point

        # Now, divide each value, and take into account the offset from zero of each range

        for line in lines:

            for point in line:
                if rotate:
                    point[0], point[1] = point[1], point[0]

                x = point[0]
                x = x - x_mid_point         # shift x values so that they have zero as their mid-point
                x = x / divider             # scale x values to fit in our box width
                x = x + box_x_mid_point     # shift x values so that they have the box x midpoint as their endpoint

                if flip ^ rotate:
                    x = -x

                point[0] = x

                y = point[1]
                y = y - y_mid_point
                y = y / divider
                y = y + box_y_mid_point

                point[1] = y

        for line in tqdm.tqdm(lines, desc="Lines", leave=False):

            if pre_start:
                pre_x, pre_y = self.pre_start_position(line[0], line[1])
                self.xy(x=pre_x, y=pre_y, wait=wait, interpolate=interpolate)

            x, y = line[0]
            self.xy(x, y)
            for point in tqdm.tqdm(line[1:], desc="Segments", leave=False):
                x, y = point
                self.xy(x, y, wait=wait, interpolate=interpolate, draw=True)

        self.park()


    def draw_line(self, start=(0, 0), end=(0, 0), wait=.5, interpolate=10, pre_start=False):
        # draws a straight line between two points

        start_x, start_y = start
        end_x, end_y = end

        if pre_start:
            pre_x, pre_y = self.pre_start_position(start, end)
            self.xy(x=pre_x, y=pre_y, wait=wait, interpolate=interpolate)

        self.xy(x=start_x, y=start_y, wait=wait, interpolate=interpolate)
        self.xy(x=end_x, y=end_y,     wait=wait, interpolate=interpolate, draw=True)


    def draw(self, x=0, y=0, wait=.5, interpolate=10):
        self.xy(x=x, y=y, wait=wait, interpolate=interpolate, draw=True)


    def pre_start_position(self, start=(0, 0), end=(0, 0)):
        # Returns an x/y position .5cm before the start of the line. Moving the pen from this point before
        # starting to draw can help eliminate "dead zones" that occur when the mechanism has to change
        # drawing direction.

        start_x, start_y = start
        end_x, end_y = end

        diff_x = start_x - end_x
        diff_y = start_y - end_y

        if diff_x:
            pre_x = start_x + (diff_x / abs(diff_x) / 2)
        else:
            pre_x = start_x

        if diff_y:
            pre_y = start_y + (diff_y / abs(diff_y) / 2)
        else:
            pre_y = start_y

        return (pre_x, pre_y)

    # ----------------- test pattern methods -----------------

    def test_pattern(self, bounds=None, wait=1, interpolate=10, repeat=1):

        bounds = bounds or self.bounds

        if not bounds:
            return "Plotting a test pattern is only possible when BrachioGraph.bounds is set."

        for r in tqdm.tqdm(tqdm.trange(repeat, desc='Iteration'), leave=False):

            for y in range(bounds[1], bounds[3], 2):

                self.xy(bounds[0],   y,     wait, interpolate)
                self.draw(bounds[2], y,     wait, interpolate)
                self.xy(bounds[2],   y + 1, wait, interpolate)
                self.draw(bounds[0], y + 1, wait, interpolate)

        self.park()


    def vertical_lines(self, bounds=None, lines=25, wait=1, interpolate=10, repeat=1, reverse=False):

        bounds = bounds or self.bounds

        if not bounds:
            return "Plotting a test pattern is only possible when BrachioGraph.bounds is set."

        if not reverse:
            top_y =    self.bounds[1]
            bottom_y = self.bounds[3]
        else:
            bottom_y = self.bounds[1]
            top_y =    self.bounds[3]

        step = (self.bounds[2] - self.bounds[0]) /  lines
        x = self.bounds[0]
        while x <= self.bounds[2]:
            self.draw_line((x, top_y), (x, bottom_y))
            x = x + step

        self.park()


    def horizontal_lines(self, bounds=None, lines=25, wait=1, interpolate=10, repeat=1, reverse=False):

        bounds = bounds or self.bounds

        if not bounds:
            return "Plotting a test pattern is only possible when BrachioGraph.bounds is set."

        if not reverse:
            min_x = self.bounds[0]
            max_x = self.bounds[2]
        else:
            max_x = self.bounds[0]
            min_x = self.bounds[2]

        step = (self.bounds[3] - self.bounds[1]) /  lines
        y = self.bounds[1]
        while y <= self.bounds[3]:
            self.draw_line((min_x, y), (max_x, y))
            y = y + step

        self.park()


    def box(self, bounds=None, wait=.15, interpolate=10, repeat=1, reverse=False):

        bounds = bounds or self.bounds

        if not bounds:
            return "Box drawing is only possible when BrachioGraph.bounds is set."

        self.xy(bounds[0], bounds[1], wait, interpolate)

        for r in tqdm.tqdm(tqdm.trange(repeat), desc='Iteration', leave=False):

            if not reverse:

                self.draw(bounds[2], bounds[1], wait, interpolate)
                self.draw(bounds[2], bounds[3], wait, interpolate)
                self.draw(bounds[0], bounds[3], wait, interpolate)
                self.draw(bounds[0], bounds[1], wait, interpolate)

            else:

                self.draw(bounds[0], bounds[3], wait, interpolate)
                self.draw(bounds[2], bounds[3], wait, interpolate)
                self.draw(bounds[2], bounds[1], wait, interpolate)
                self.draw(bounds[0], bounds[1], wait, interpolate)

        self.park()


    # ----------------- pen-moving methods -----------------


    def centre(self):

        if not self.bounds:
            return "Moving to the centre is only possible when BrachioGraph.bounds is set."

        self.pen.up()
        self.xy(self.bounds[2]/2, self.bounds[3]/2)

        self.quiet()


    def xy(self, x=0, y=0, wait=.1, interpolate=10, draw=False):
        # Moves the pen to the xy position; optionally draws

        if draw:
            self.pen.down()
        else:
            self.pen.up()

        (angle_1, angle_2) = self.xy_to_angles(x, y)
        (pulse_width_1, pulse_width_2) = self.angles_to_pulse_widths(angle_1, angle_2)

        # if they are the same, we don't need to move anything
        if (pulse_width_1, pulse_width_2) == self.get_pulse_widths():

            # ensure the pantograph knows its x/y positions
            self.current_x = x
            self.current_y = y

            return

        # we assume the pantograph knows its x/y positions - if not, there could be
        # a sudden movement later

        # calculate how many steps we need for this move, and the x/y length of each
        (x_length, y_length) = (x - self.current_x, y - self.current_y)

        length = math.sqrt(x_length ** 2 + y_length **2)

        no_of_steps = int(length * interpolate) or 1

        if no_of_steps < 100:
            disable_tqdm = True
        else:
            disable_tqdm = False


        (length_of_step_x, length_of_step_y) = (x_length/no_of_steps, y_length/no_of_steps)

        for step in tqdm.tqdm(range(no_of_steps), desc='Interpolation', leave=False, disable=disable_tqdm):

            self.current_x = self.current_x + length_of_step_x
            self.current_y = self.current_y + length_of_step_y

            angle_1, angle_2 = self.xy_to_angles(self.current_x, self.current_y)

            self.set_angles(angle_1, angle_2)

            if step + 1 < no_of_steps:
                sleep(length * wait/no_of_steps)

        sleep(length * wait/10)


    def set_angles(self, angle_1=0, angle_2=0):
        # moves the servo motor

        pw_1, pw_2 = self.angles_to_pulse_widths(angle_1, angle_2)

        self.set_pulse_widths(pw_1, pw_2)

        # We record the angles, so we that we know where the arms are for future reference.
        self.angle_1, self.angle_2 = angle_1, angle_2

        self.angles_used_1.add(angle_1)
        self.angles_used_2.add(angle_2)
        self.pulse_widths_used_1.add(pw_1)
        self.pulse_widths_used_2.add(pw_2)


    #  ----------------- hardware-related methods -----------------

    def naive_angles_to_pulse_widths_1(self, angle):
        return (angle + 90) * self.servo_1_degree_ms + self.servo_1_zero

    def naive_angles_to_pulse_widths_2(self, angle):
        return (angle - 90) * self.servo_2_degree_ms + self.servo_2_zero


    def angles_to_pulse_widths(self, angle_1, angle_2):
        # Given a pair of angles, returns the appropriate pulse widths.

        # at present we assume only one method of calculating, using the angles_to_pw_1 and angles_to_pw_2
        # functions created using numpy

        pulse_width_1, pulse_width_2 = self.angles_to_pw_1(angle_1), self.angles_to_pw_2(angle_2)

        return (pulse_width_1, pulse_width_2)


    def set_pulse_widths(self, pw_1, pw_2):

        if self.virtual_mode:

            if (500 < pw_1 < 2500) and (500 < pw_2 < 2500):

                self.virtual_pw_1 = self.angles_to_pw_1(pw_1)
                self.virtual_pw_2 = self.angles_to_pw_2(pw_2)

            else:
               raise ValueError

        else:

            self.rpi.set_servo_pulsewidth(14, pw_1)
            self.rpi.set_servo_pulsewidth(15, pw_2)


    def get_pulse_widths(self):

        if self.virtual_mode:

            actual_pulse_width_1 = self.virtual_pw_1
            actual_pulse_width_2 = self.virtual_pw_2

        else:

            actual_pulse_width_1 = self.rpi.get_servo_pulsewidth(14)
            actual_pulse_width_2 = self.rpi.get_servo_pulsewidth(15)

        return (actual_pulse_width_1, actual_pulse_width_2)


    def park(self):

        # parks the plotter

        if self.virtual_mode:
            print("Parking")

        self.pen.up()
        self.xy(-self.INNER_ARM, self.OUTER_ARM)
        sleep(1)
        self.quiet()


    def quiet(self, servos=[14, 15, 18]):

        # stop sending pulses to the servos

        if self.virtual_mode:
            print("Going quiet")

        else:

            for servo in servos:
                self.rpi.set_servo_pulsewidth(servo, 0)


    # ----------------- trigonometric methods -----------------

    # Every x/y position of the plotter corresponds to a pair of angles of the arms. These methods
    # calculate:
    #
    # the angles required to reach any x/y position
    # the x/y position represented by any pair of angles

    def xy_to_angles(self, x=0, y=0):

        # convert x/y co-ordinates into motor angles

        hypotenuse = math.sqrt(x**2+y**2)
        hypotenuse_angle = math.asin(x/hypotenuse)

        inner_angle = math.acos(
            (hypotenuse**2+self.INNER_ARM**2-self.OUTER_ARM**2)/(2*hypotenuse*self.INNER_ARM)
        )
        outer_angle = math.acos(
            (self.INNER_ARM**2+self.OUTER_ARM**2-hypotenuse**2)/(2*self.INNER_ARM*self.OUTER_ARM)
        )

        shoulder_motor_angle = hypotenuse_angle - inner_angle
        elbow_motor_angle = math.pi - outer_angle

        return (math.degrees(shoulder_motor_angle), math.degrees(elbow_motor_angle))


    def angles_to_xy(self, shoulder_motor_angle, elbow_motor_angle):

        # convert motor angles into x/y co-ordinates

        elbow_motor_angle = math.radians(elbow_motor_angle)
        shoulder_motor_angle = math.radians(shoulder_motor_angle)

        hypotenuse = math.sqrt(
            (self.INNER_ARM ** 2 + self.OUTER_ARM ** 2 - 2 * self.INNER_ARM * self.OUTER_ARM * math.cos(
                math.pi - elbow_motor_angle)
            )
        )
        base_angle = math.acos(
            (hypotenuse ** 2 + self.INNER_ARM ** 2 - self.OUTER_ARM ** 2) / (2 * hypotenuse * self.INNER_ARM)
        )
        inner_angle = base_angle + shoulder_motor_angle

        x = math.sin(inner_angle) * hypotenuse
        y = math.cos(inner_angle) * hypotenuse

        return(x, y)


    # ----------------- manual driving methods -----------------

    def drive(self):

        # adjust the pulse-widths using the keyboard

        pw_1, pw_2 = self.get_pulse_widths()

        self.set_pulse_widths(pw_1, pw_2)

        while True:
            key = readchar.readchar()

            if key == "0":
                return
            elif key=="a":
                pw_1 = pw_1 - 10
            elif key=="s":
                pw_1 = pw_1 + 10
            elif key=="A":
                pw_1 = pw_1 - 1
            elif key=="S":
                pw_1 = pw_1 + 1
            elif key=="k":
                pw_2 = pw_2 - 10
            elif key=="l":
                pw_2 = pw_2 + 10
            elif key=="K":
                pw_2 = pw_2 - 1
            elif key=="L":
                pw_2 = pw_2 + 1

            print(pw_1, pw_2)

            self.set_pulse_widths(pw_1, pw_2)


    def drive_xy(self):

        # move the pen up/down and left/right using the keyboard

        while True:
            key = readchar.readchar()

            if key == "0":
                return
            elif key=="a":
                self.current_x = self.current_x - 1
            elif key=="s":
                self.current_x = self.current_x + 1
            elif key=="A":
                self.current_x = self.current_x - .1
            elif key=="S":
                self.current_x = self.current_x + .1
            elif key=="k":
                self.current_y = self.current_y - 1
            elif key=="l":
                self.current_y = self.current_y + 1
            elif key=="K":
                self.current_y = self.current_y - .1
            elif key=="L":
                self.current_y = self.current_y + .1

            print(self.current_x, self.current_y)

            self.xy(self.current_x, self.current_y)


    # ----------------- reporting methods -----------------

    def report(self):

        if self.angles_used_1 and self.angles_used_2 and self.pulse_widths_used_1 and self.pulse_widths_used_2:

            print(f"                   Servo 1            Servo 2 ")
            print(f"               min   max   mid    min   max   mid")

            min1 = min(self.angles_used_1)
            max1 = max(self.angles_used_1)
            mid1 = (min1 + max1) / 2
            min2 = min(self.angles_used_2)
            max2 = max(self.angles_used_2)
            mid2 = (min2 + max2) / 2

            print(f"      angles  {min1:>4.0f}  {max1:>4.0f}  {mid1:>4.0f}   {min2:>4.0f}  {max2:>4.0f}  {mid2:>4.0f}")

            min1 = min(self.pulse_widths_used_1)
            max1 = max(self.pulse_widths_used_1)
            mid1 = (min1 + max1) / 2
            min2 = min(self.pulse_widths_used_2)
            max2 = max(self.pulse_widths_used_2)
            mid2 = (min2 + max2) / 2

            print(f"pulse-widths  {min1:>4.0f}  {max1:>4.0f}  {mid1:>4.0f}   {min2:>4.0f}  {max2:>4.0f}  {mid2:>4.0f}")

        else:

            print("No data recorded yet. Try calling the BrachioGraph.box() method first.")


class Pen:

    def __init__(self, bg, pw_up=1500, pw_down=1100, pin=18, transition_time=0.25):

        self.bg = bg
        self.pin = pin
        self.pw_up = pw_up
        self.pw_down = pw_down
        self.transition_time = transition_time

        if bg.virtual_mode:

            print("Initialising virtual Pen")
            self.virtual_mode = True

        else:

            self.rpi = pigpio.pi()
            self.rpi.set_PWM_frequency(self.pin, 50)

        self.up()
        sleep(0.3)
        self.down()
        sleep(0.3)
        self.up()
        sleep(0.3)


    def down(self):

        if self.virtual_mode:

            self.virtual_pw = self.pw_down

        else:

            self.rpi.set_servo_pulsewidth(self.pin, self.pw_down)
            sleep(self.transition_time)


    def up(self):

        if self.virtual_mode:

            self.virtual_pw = self.pw_up

        else:

            self.rpi.set_servo_pulsewidth(self.pin, self.pw_up)
            sleep(self.transition_time)


    # for convenience, a quick way to set pen motor pulse-widths
    def pw(self, pulse_width):

        if self.virtual_mode:

            self.virtual_pw = pulse_width

        else:

            self.rpi.set_servo_pulsewidth(self.pin, pulse_width)

