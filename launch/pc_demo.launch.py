"""Launch the PC-only real-adapter Mission Manager composition."""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description() -> LaunchDescription:
    """Create the PC Demo entry point without changing Navigation config."""
    return LaunchDescription([
        DeclareLaunchArgument(
            'use_sim_time',
            default_value='true',
            description='Use the Gazebo simulation clock when true.',
        ),
        DeclareLaunchArgument(
            'demo_goal_x',
            default_value='-4.0',
            description='Demo-only task30 goal X in the map frame.',
        ),
        DeclareLaunchArgument(
            'demo_goal_y',
            default_value='0.0',
            description='Demo-only task30 goal Y in the map frame.',
        ),
        DeclareLaunchArgument(
            'demo_goal_yaw',
            default_value='0.0',
            description='Demo-only task30 goal yaw in radians.',
        ),
        Node(
            package='cleannav_mission_manager',
            executable='cleannav_pc_demo',
            name='mission_manager_pc_demo',
            output='screen',
            parameters=[{
                'use_sim_time': LaunchConfiguration('use_sim_time'),
                'demo_goal_x': ParameterValue(
                    LaunchConfiguration('demo_goal_x'),
                    value_type=float,
                ),
                'demo_goal_y': ParameterValue(
                    LaunchConfiguration('demo_goal_y'),
                    value_type=float,
                ),
                'demo_goal_yaw': ParameterValue(
                    LaunchConfiguration('demo_goal_yaw'),
                    value_type=float,
                ),
            }],
        ),
    ])
