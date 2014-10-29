.. _edX Platform API Endpoints:

################################################
edX LMS Platform Endpoints
################################################

The edX Platform API allows you to view information about users and their course enrollments, course information, and videos and transcripts.

The following tasks and endpoints are currently supported. 


.. list-table::
   :widths: 10 70
   :header-rows: 1

   * - To:
     - Use this endpoint:
   * - :ref:`Get details about a user<Get User Details>`
     - /api/mobile/v0.5/users/{username}
   * - :ref:`Get course enrollments for about a user<Get a User's Course Enrollments>`
     - /api/mobile/v0.5/users/{username}/course_enrollments/
   * - :ref:`Get a course About page<Get the Course About Page>`
     - /api/mobile/v0.5/course_info/{organization}/{course_number}/{course_run}/about
   * - :ref:`Get updates for a course<Get Course Updates>`
     - /api/mobile/v0.5/course_info/{organization}/{course_number}/{course_run}/updates   
   * - :ref:`Get handouts for a course<Get Course Handouts>`
     - /api/mobile/v0.5/course_info/{organization}/{course_number}/{course_run}/handouts
   * - :ref:`Get videos in a course<Get the Video List>`
     - /api/mobile/v0.5/video_outlines/courses/{organization}/{course_number}/{course_run}
   * - :ref:`Get a video transcript<Get a Video Transcript>`
     - /api/mobile/v0.5/video_outlines/transcripts/{organization}/{course_number}/{course_run}/{video ID}/{language code}
